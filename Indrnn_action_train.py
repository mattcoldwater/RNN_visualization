from __future__ import print_function
import sys
import argparse
import os
import time
import numpy as np
import copy
import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F


# Set the random seed manually for reproducibility.
seed=100
torch.manual_seed(seed)
CUDA = False
if torch.cuda.is_available():
  torch.cuda.manual_seed(seed)
  CUDA = True
else:
  print("WARNING: CUDA not available")
  CUDA = False

import opts     
parser = argparse.ArgumentParser(description='pytorch action')
opts.train_opts(parser)
args = parser.parse_args()
print(args)

import Indrnn_action_network

batch_size = args.batch_size
print('BATCH_SIZE', batch_size)
seq_len=args.seq_len
outputclass=60
indim=50
gradientclip_value=10
U_bound=Indrnn_action_network.U_bound



model = Indrnn_action_network.stackedIndRNN_encoder(indim, outputclass, CUDA)  
if CUDA:
    model.cuda()
criterion = nn.CrossEntropyLoss()

#Adam with lr 2e-4 works fine.
learning_rate=args.lr
if args.use_weightdecay_nohiddenW:
  param_decay=[]
  param_nodecay=[]
  for name, param in model.named_parameters():
    if 'weight_hh' in name or 'bias' in name:
      param_nodecay.append(param)      
      #print('parameters no weight decay: ',name)          
    else:
      param_decay.append(param)      
      #print('parameters with weight decay: ',name)          

  if args.opti=='sgd':
    optimizer = torch.optim.SGD([
            {'params': param_nodecay},
            {'params': param_decay, 'weight_decay': args.decayfactor}
        ], lr=learning_rate,momentum=0.9,nesterov=True)   
  else:                
    optimizer = torch.optim.Adam([
            {'params': param_nodecay},
            {'params': param_decay, 'weight_decay': args.decayfactor}
        ], lr=learning_rate) 
else:  
  if args.opti=='sgd':   
    optimizer=torch.optim.Adam(model.parameters(), lr=learning_rate,momentum=0.9,nesterov=True)
  else:                      
    optimizer=torch.optim.Adam(model.parameters(), lr=learning_rate)

if args.test_CV:
  train_datasets='train_CV_ntus'
  test_dataset='test_CV_ntus'
else:
  train_datasets='train_ntus'
  test_dataset='test_ntus'
  
#from data_reader_numpy_witheval import DataHandler_train,DataHandler_eval  
#from data_reader_numpy_test import DataHandler as testDataHandler

from data_reader_ntu import initialize_data_handlers

dh_train, dh_eval, dh_test = initialize_data_handlers(batch_size, seq_len)

#dh_train = DataHandler_train(batch_size,seq_len)
#dh_eval = DataHandler_eval(batch_size,seq_len)
#dh_test= testDataHandler(batch_size,seq_len)

num_train_batches=int(np.ceil(dh_train.GetDatasetSize()/(batch_size+0.0)))
num_eval_batches=int(np.ceil(dh_eval.GetDatasetSize()/(batch_size+0.0)))
num_test_batches=int(np.ceil(dh_test.GetDatasetSize()/(batch_size+0.0)))


def train(num_train_batches):
  model.train()
  tacc=0
  count=0
  start_time = time.time()
  for batchi in range(0,num_train_batches):
    inputs,targets=dh_train.GetBatch()
    inputs=inputs.transpose(1,0,2,3)
    
    if CUDA:
        inputs=Variable(torch.from_numpy(inputs).cuda(), requires_grad=True).float()
        targets=Variable(torch.from_numpy(np.int64(targets)).cuda(), requires_grad=False)
    else:
        inputs=Variable(torch.from_numpy(inputs), requires_grad=True).float()
        targets=Variable(torch.from_numpy(np.int64(targets)), requires_grad=False)

    model.zero_grad()
    if args.constrain_U:
      clip_weight(model,U_bound)
    output=model(inputs)
    loss = criterion(output, targets)

    pred = output.data.max(1)[1] # get the index of the max log-probability
    accuracy = pred.eq(targets.data).cpu().sum().numpy()/(0.0+targets.size(0))      
          
    loss.backward()
    clip_gradient(model,gradientclip_value)
    optimizer.step()
    
    tacc=tacc+accuracy#loss.data.cpu().numpy()#accuracy
    count+=1
  elapsed = time.time() - start_time
  print ("training accuracy: ", tacc/(count+0.0)  )
  #print ('time per batch: ', elapsed/num_train_batches)
  
def set_bn_train(m):
    classname = m.__class__.__name__
    if classname.find('BatchNorm') != -1:
      m.train()       
def eval(dh,num_batches,use_bn_trainstat=False):
  model.eval()
  if use_bn_trainstat:
    model.apply(set_bn_train)
  tacc=0
  count=0  
  start_time = time.time()
  while(1):  
    inputs,targets=dh.GetBatch()
    inputs=inputs.transpose(1,0,2,3)
    if CUDA:
        inputs=Variable(torch.from_numpy(inputs).cuda()).float()
        targets=Variable(torch.from_numpy(np.int64(targets)).cuda())
    else: 
        inputs=Variable(torch.from_numpy(inputs)).float()
        targets=Variable(torch.from_numpy(np.int64(targets)))
    output=model(inputs)
    pred = output.data.max(1)[1] # get the index of the max log-probability
    accuracy = pred.eq(targets.data).cpu().sum().numpy()        
    tacc+=accuracy
    count+=1
    if count==num_batches*args.eval_fold:
      break
  elapsed = time.time() - start_time
  print ("eval accuracy: ", tacc/(count*targets.data.size(0)+0.0)  )
  #print ('eval time per batch: ', elapsed/(count+0.0))
  return tacc/(count*targets.data.size(0)+0.0)


def test(dh,num_batches,use_bn_trainstat=False):
  model.eval()
  if use_bn_trainstat:
    model.apply(set_bn_train)
  tacc=0
  count=0  
  start_time = time.time()
  total_testdata=dh_test.GetDatasetSize()  
  total_ave_acc = None
  test_labels = None
  while(1):  
    inputs,targets=dh.GetBatch()
    inputs=inputs.transpose(1,0,2,3)
    if test_labels is None:
        test_labels = targets
    else:
        test_labels=np.concatenate(
                [test_labels, targets], 
                axis=0,
                )
    if CUDA:
        inputs=Variable(torch.from_numpy(inputs).cuda()).float()
        targets=Variable(torch.from_numpy(np.int64(targets)).cuda())
    else:
        inputs=Variable(torch.from_numpy(inputs)).float()
        targets=Variable(torch.from_numpy(np.int64(targets)))
        
    output=model(inputs)
    pred = output.data.max(1)[1] # get the index of the max log-probability
    accuracy = pred.eq(targets.data).cpu().sum().numpy()    
    
    if not total_ave_acc is None :
        total_ave_acc = np.concatenate(
                [total_ave_acc, output.data.cpu().numpy()]
                , axis=0,
                )
    else:
        total_ave_acc = output.data.cpu().numpy()
    
    tacc+=accuracy
    count+=1
    if count==num_batches:
      break    
  top = np.argmax(total_ave_acc, axis=-1)
  eval_acc=np.mean(np.equal(top, test_labels))    
  elapsed = time.time() - start_time
  print ("test accuracy: ",  eval_acc)
  #print ('test time per batch: ', elapsed/(count+0.0))
  return eval_acc

def clip_gradient(model, clip):
    for p in model.parameters():
        p.grad.data.clamp_(-clip,clip)
        #print(p.size(),p.grad.data)

def adjust_learning_rate(optimizer, lr):
    """Sets the learning rate to the initial LR decayed by 10 every 30 epochs"""
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr     

def clip_weight(RNNmodel, clip):
    for name, param in RNNmodel.named_parameters():
      if 'weight_hh' in name:
        param.data.clamp_(-clip,clip)
    
lastacc=0
dispFreq=20
patience=0
reduced=1
for batchi in range(1,10000000):
  for i in range(num_train_batches//dispFreq):

    train(dispFreq)
  test_acc=eval(dh_eval,num_eval_batches,args.use_bneval)
  
  if (test_acc >lastacc):
    model_clone = copy.deepcopy(model.state_dict())   
    opti_clone = copy.deepcopy(optimizer.state_dict()) 
    lastacc=test_acc
    patience=0
  elif patience>int(args.pThre/reduced+0.5):
    reduced=reduced*2
    print ('learning rate',learning_rate)
    model.load_state_dict(model_clone)
    optimizer.load_state_dict(opti_clone)
    patience=0
    learning_rate=learning_rate*0.1
    adjust_learning_rate(optimizer,learning_rate)     
    if learning_rate<args.end_rate:
      break  
    test_acc=test(dh_test,num_test_batches)     
 
  else:
    patience+=1 
    
test_acc=test(dh_test,num_test_batches)  
test_acc=test(dh_test,num_test_batches,True)     
save_name='indrnn_action_model' 
with open(save_name, 'wb') as f:
    torch.save(model, f)



 
