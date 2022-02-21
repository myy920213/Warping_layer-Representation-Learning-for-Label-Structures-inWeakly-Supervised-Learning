# -*- coding: utf-8 -*-
import os
import argparse
import random
import numpy as np
from tqdm import tqdm
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torch.autograd import Variable

from models.classification_heads import * #ClassificationHead
from models.R2D2_embedding import R2D2Embedding
from models.protonet_embedding import ProtoNetEmbedding, ProtoNetEmbedding_N, ProtoNetEmbedding_Opt
from models.ResNet12_embedding import resnet12

from utils import set_gpu, Timer, count_accuracy, check_dir, log

def one_hot(indices, depth):
    """
    Returns a one-hot tensor.
    This is a PyTorch equivalent of Tensorflow's tf.one_hot.
        
    Parameters:
      indices:  a (n_batch, m) Tensor or (m) Tensor.
      depth: a scalar. Represents the depth of the one hot dimension.
    Returns: a (n_batch, m, depth) Tensor or (m, depth) Tensor.
    """

    encoded_indicies = torch.zeros(indices.size() + torch.Size([depth])).to(device)
    index = indices.view(indices.size()+torch.Size([1]))
    encoded_indicies = encoded_indicies.scatter_(1,index,1)
    
    return encoded_indicies

def get_model(options):
    # Choose the embedding network
    if options.network == 'ProtoNet':
        if opt.dataset == 'miniImageNet':
            out_channels = 1600
        else:
            out_channels = 256
        if options.normalize:
            network = ProtoNetEmbedding_N(out_channels = out_channels).to(device)
        else:
            network = ProtoNetEmbedding(out_channels = out_channels).to(device)
        if options.pre_train:
            pre_train = torch.load(options.pre_train)
            network.load_state_dict(pre_train['embedding'])
            print("=> loaded checkpoint '{}' )".format(options.pre_train))
        network_opt = ProtoNetEmbedding_Opt(config, network).to(device)
    elif options.network == 'R2D2':
        network = R2D2Embedding().to(device)
    elif options.network == 'ResNet':
        if options.dataset == 'miniImageNet' or options.dataset == 'tieredImageNet':
            network = resnet12(avg_pool=False, drop_rate=0.1, dropblock_size=5).to(device)
            network = torch.nn.DataParallel(network, device_ids=[0, 1, 2, 3])
        else:
            network = resnet12(avg_pool=False, drop_rate=0.1, dropblock_size=2).to(device)
    else:
        print ("Cannot recognize the network type")
        assert(False)
    
    
        
    # Choose the classification head
    if options.head == 'ProtoNet':
        cls_head = ClassificationHead(base_learner='ProtoNet').to(device)
    elif options.head == 'Ridge':
        cls_head = ClassificationHead(base_learner='Ridge').to(device)
    elif options.head == 'R2D2':
        cls_head = ClassificationHead(base_learner='R2D2').to(device)
    elif options.head == 'SVM':
        cls_head = ClassificationHead(base_learner='SVM-CS').to(device)
    else:
        print ("Cannot recognize the dataset type")
        assert(False)
        
    return (network_opt, cls_head)

def get_dataset(options):
    # Choose the embedding network
    if options.dataset == 'miniImageNet':
        from data.mini_imagenet import MiniImageNet, FewShotDataloader
        dataset_train = MiniImageNet(phase='train')
        dataset_val = MiniImageNet(phase='test')
        data_loader = FewShotDataloader
    elif options.dataset == 'tieredImageNet':
        from data.tiered_imagenet import tieredImageNet, FewShotDataloader
        dataset_train = tieredImageNet(phase='train')
        dataset_val = tieredImageNet(phase='val')
        data_loader = FewShotDataloader
    elif options.dataset == 'CIFAR_FS':
        from data.CIFAR_FS import CIFAR_FS, FewShotDataloader
        dataset_train = CIFAR_FS(phase='train')
        dataset_val = CIFAR_FS(phase='val')
        data_loader = FewShotDataloader
    elif options.dataset == 'FC100':
        from data.FC100 import FC100, FewShotDataloader
        dataset_train = FC100(phase='train')
        dataset_val = FC100(phase='val')
        data_loader = FewShotDataloader
    else:
        print ("Cannot recognize the dataset type")
        assert(False)
        
    return (dataset_train, dataset_val, data_loader)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--num-epoch', type=int, default=60,
                            help='number of training epochs')
    parser.add_argument('--save-epoch', type=int, default=10,
                            help='frequency of model saving')
    parser.add_argument('--train-shot', type=int, default=15,
                            help='number of support examples per training class')
    parser.add_argument('--val-shot', type=int, default=5,
                            help='number of support examples per validation class')
    parser.add_argument('--train-query', type=int, default=6,
                            help='number of query examples per training class')
    parser.add_argument('--val-episode', type=int, default=2000,
                            help='number of episodes per validation')
    parser.add_argument('--val-query', type=int, default=15,
                            help='number of query examples per validation class')
    parser.add_argument('--train-way', type=int, default=5,
                            help='number of classes in one training episode')
    parser.add_argument('--test-way', type=int, default=5,
                            help='number of classes in one test (or validation) episode')
    parser.add_argument('--save-path', type = str, default='./experiments/exp_1')
    parser.add_argument('--gpu', type=str, default='0, 1, 2, 3')
    parser.add_argument('--cpu', type=int, default=0)
    parser.add_argument('--network', type=str, default='ProtoNet',
                            help='choose which embedding network to use. ProtoNet, R2D2, ResNet')
    parser.add_argument('--head', type=str, default='ProtoNet',
                            help='choose which classification head to use. ProtoNet, Ridge, R2D2, SVM')
    parser.add_argument('--dataset', type=str, default='miniImageNet',
                            help='choose which classification head to use. miniImageNet, tieredImageNet, CIFAR_FS, FC100')
    parser.add_argument('--episodes-per-batch', type=int, default=8,
                            help='number of episodes per batch')
    parser.add_argument('--eps', type=float, default=0.0,
                            help='epsilon of label smoothing')
    parser.add_argument('--num_batch', type=int, default=1000,
                            help='number of batches per epoch')
    parser.add_argument('--normalize', type=int, default=1,
                            help='normalize output')
    parser.add_argument('--lr', type=float, default=0.1,
                            help='initial learning rate')
    parser.add_argument('--pre_train', type=str, default=None, help='pre_train model path')
    parser.add_argument('--gamma', type=float, default=1.0,
                            help='tardeoff parameter')
    parser.add_argument('--a_clip_max', type=float, default=0.000625,
                            help='max value of a clip')
    parser.add_argument('--a_clip_min', type=float, default=0.000625,
                            help='min va;ue of a clip')
    parser.add_argument('--print-every', type=int, default=100,
                            help='print every n episodes')
    parser.add_argument('--lr-epoch', type=str, default='20 40 50', help='epochs for learning rate schedule')
    parser.add_argument('--lr-val', type=str, default='1.0 0.06 0.012 0.0024', help='values for adjusted learning rate')



    opt = parser.parse_args()
    
    (dataset_train, dataset_val, data_loader) = get_dataset(opt)
    #print('novel:', dataset_train.labelIds_novel)
    # Dataloader of Gidaris & Komodakis (CVPR 2018)
    dloader_train = data_loader(
        dataset=dataset_train,
        nKnovel=opt.train_way,
        nKbase=0,
        nExemplars=opt.train_shot, # num training examples per novel category
        nTestNovel=opt.train_way * opt.train_query, # num test examples for all the novel categories
        nTestBase=0, # num test examples for all the base categories
        batch_size=opt.episodes_per_batch,
        num_workers=4,
        epoch_size=opt.episodes_per_batch * opt.num_batch, # num of batches per epoch
    )

    dloader_val = data_loader(
        dataset=dataset_val,
        nKnovel=opt.test_way,
        nKbase=0,
        nExemplars=opt.val_shot, # num training examples per novel category
        nTestNovel=opt.val_query * opt.test_way, # num test examples for all the novel categories
        nTestBase=0, # num test examples for all the base categories
        batch_size=1,
        num_workers=0,
        epoch_size=1 * opt.val_episode, # num of batches per epoch
    )
    global device
    device = ''
    if opt.cpu:
        device = 'cpu'
    else:
        set_gpu(opt.gpu)
        device = 'cuda'
    config = {}
    if opt.dataset == 'miniImageNet':
        num_centers = 120
        Phi = torch.load('pre_stores/mini_imagenet/mini_imagenet_out.pt')
        Phi_matrix = torch.transpose(Phi, 0, 1) @ Phi / num_centers
        config = {'device': device,
        'gamma': opt.gamma,
        'a_value': 0.000625,
        'nclass': 144,
        'exc': torch.from_numpy(np.load('pre_stores/mini_imagenet/exc_matrix.npy')).float(),
        'imp': torch.from_numpy(np.load('pre_stores/mini_imagenet/imp_matrix.npy')).float(),
        'Phi': Phi_matrix,
        'centers': torch.load('pre_stores/mini_imagenet/mini_imagenet_raw.pt').to(device)}
    elif opt.dataset == 'CIFAR_FS':
        num_centers = 240
        Phi = torch.load('pre_stores/cifar_fs/cifar_fs_out.pt')
        Phi_matrix = torch.transpose(Phi, 0, 1) @ Phi / num_centers
        config = {'device': device,
        'gamma': opt.gamma,
        'a_value': 0.004,
        'nclass': 150,
        'exc': torch.from_numpy(np.load('pre_stores/cifar_fs/exc_matrix.npy')).float(),
        'imp': torch.from_numpy(np.load('pre_stores/cifar_fs/imp_matrix.npy')).float(),
        'Phi': Phi_matrix,
        'centers': torch.load('pre_stores/cifar_fs/cifar_fs_raw.pt').to(device)}
    check_dir('./experiments/')
    check_dir(opt.save_path)
    
    log_file_path = os.path.join(opt.save_path, "train_log.txt")
    log(log_file_path, str(vars(opt)))

    (embedding_net, cls_head) = get_model(opt)
    
    optimizer = torch.optim.SGD([{'params': embedding_net.parameters()}, 
                                 {'params': cls_head.parameters()}], lr=opt.lr, momentum=0.9, \
                                          weight_decay=5e-4, nesterov=True)
    lr_epoch = opt.lr_epoch.split()
    print('lr-epoch', lr_epoch)
    lr_val = opt.lr_val.split()
    print('lr-val', lr_val)
    lambda_epoch = lambda e: float(lr_val[0]) if e < int(lr_epoch[0]) else (float(lr_val[1]) if e < int(lr_epoch[1]) else float(lr_val[2]) if e < int(lr_epoch[2]) else (float(lr_val[3])))
    lr_scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lambda_epoch, last_epoch=-1)

    max_val_acc = 0.0

    timer = Timer()
    x_entropy = torch.nn.CrossEntropyLoss()
    
    for epoch in range(1, opt.num_epoch + 1):
        # Train on the training split
        lr_scheduler.step()
        
        # Fetch the current epoch's learning rate
        epoch_learning_rate = opt.lr
        for param_group in optimizer.param_groups:
            epoch_learning_rate = param_group['lr']
            
        log(log_file_path, 'Train Epoch: {}\tLearning Rate: {:.4f}'.format(
                            epoch, epoch_learning_rate))
        
        _, _ = [x.train() for x in (embedding_net, cls_head)]
        
        train_accuracies = []
        train_losses = []

        for i, batch in enumerate(tqdm(dloader_train(epoch)), 1):
            #print('iter',i)
            cur_Phi = embedding_net.base(config['centers']).detach().cpu()
            embedding_net.OptNet.Phi = torch.transpose(cur_Phi, 0, 1) @ cur_Phi / num_centers
            data_support, labels_support, data_query, labels_query, _, _ = [x.to(device) for x in batch]
            #print('labels', labels_support)
            train_n_support = opt.train_way * opt.train_shot
            train_n_query = opt.train_way * opt.train_query
            #print('input_size', (data_support.reshape([-1] + list(data_support.shape[-3:]))).size())
            emb_support = embedding_net(data_support.reshape([-1] + list(data_support.shape[-3:])))
            emb_support = emb_support.reshape(opt.episodes_per_batch, train_n_support, -1)
            #print('support_size', emb_support.size())
            #print('norm1', torch.norm(emb_support[0][0]))
            #print('norm2', torch.norm(emb_support[1][0]))
            emb_query = embedding_net(data_query.reshape([-1] + list(data_query.shape[-3:])))
            emb_query = emb_query.reshape(opt.episodes_per_batch, train_n_query, -1)
            #print('query_size', emb_query.size())
            
            logit_query = cls_head(emb_query, emb_support, labels_support, opt.train_way, opt.train_shot)

            smoothed_one_hot = one_hot(labels_query.reshape(-1), opt.train_way)
            smoothed_one_hot = smoothed_one_hot * (1 - opt.eps) + (1 - smoothed_one_hot) * opt.eps / (opt.train_way - 1)

            log_prb = F.log_softmax(logit_query.reshape(-1, opt.train_way), dim=1)
            loss = -(smoothed_one_hot * log_prb).sum(dim=1)
            loss = loss.mean()
            #print('loss', loss)
            #print('a', embedding_net.OptNet.a)
            
            acc = count_accuracy(logit_query.reshape(-1, opt.train_way), labels_query.reshape(-1))
            
            train_accuracies.append(acc.item())
            train_losses.append(loss.item())

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            with torch.no_grad():
                embedding_net.OptNet.a.data = embedding_net.OptNet.a.clamp(opt.a_clip_min, opt.a_clip_max)


            if (i % opt.print_every == 0):
                train_acc_avg = np.mean(np.array(train_accuracies))
                log(log_file_path, 'Train Epoch: {}\tBatch: [{}/{}]\tLoss: {:.4f}\tAccuracy: {:.2f} % ({:.2f} %)'.format(
                            epoch, i, len(dloader_train), loss.item(), train_acc_avg, acc))
            
            
                # Evaluate on the validation split
                _, _ = [x.eval() for x in (embedding_net, cls_head)]

                val_accuracies = []
                val_losses = []
                
                for i, batch in enumerate(tqdm(dloader_val(epoch)), 1):
                    data_support, labels_support, data_query, labels_query, _, _ = [x.to(device) for x in batch]

                    test_n_support = opt.test_way * opt.val_shot
                    test_n_query = opt.test_way * opt.val_query

                    emb_support = embedding_net(data_support.reshape([-1] + list(data_support.shape[-3:])))
                    emb_support = emb_support.reshape(1, test_n_support, -1)

                    emb_query = embedding_net(data_query.reshape([-1] + list(data_query.shape[-3:])))
                    emb_query = emb_query.reshape(1, test_n_query, -1)

                    logit_query = cls_head(emb_query, emb_support, labels_support, opt.test_way, opt.val_shot)

                    loss = x_entropy(logit_query.reshape(-1, opt.test_way), labels_query.reshape(-1))
                    acc = count_accuracy(logit_query.reshape(-1, opt.test_way), labels_query.reshape(-1))

                    val_accuracies.append(acc.item())
                    val_losses.append(loss.item())
                    
                val_acc_avg = np.mean(np.array(val_accuracies))
                val_acc_ci95 = 1.96 * np.std(np.array(val_accuracies)) / np.sqrt(opt.val_episode)

                val_loss_avg = np.mean(np.array(val_losses))

                if val_acc_avg > max_val_acc:
                    max_val_acc = val_acc_avg
                    torch.save({'embedding': embedding_net.state_dict(), 'head': cls_head.state_dict()},\
                            os.path.join(opt.save_path, 'best_model.pth'))
                    log(log_file_path, 'Validation Epoch: {}\t\t\tLoss: {:.4f}\tAccuracy: {:.2f} ± {:.2f} % (Best)'\
                        .format(epoch, val_loss_avg, val_acc_avg, val_acc_ci95))
                else:
                    log(log_file_path, 'Validation Epoch: {}\t\t\tLoss: {:.4f}\tAccuracy: {:.2f} ± {:.2f} %'\
                        .format(epoch, val_loss_avg, val_acc_avg, val_acc_ci95))

                torch.save({'embedding': embedding_net.state_dict(), 'head': cls_head.state_dict()}\
                        , os.path.join(opt.save_path, 'last_epoch.pth'))

                if epoch % opt.save_epoch == 0:
                    torch.save({'embedding': embedding_net.state_dict(), 'head': cls_head.state_dict()}\
                            , os.path.join(opt.save_path, 'epoch_{}.pth'.format(epoch)))

                log(log_file_path, 'Elapsed Time: {}/{}\n'.format(timer.measure(), timer.measure(epoch / float(opt.num_epoch))))
                _, _ = [x.train() for x in (embedding_net, cls_head)]