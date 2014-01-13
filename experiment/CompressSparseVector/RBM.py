# coding: utf-8

# general library imports
import cPickle, gzip, time, os, sys, pdb, json, datetime, curses
import numpy
import scipy.sparse
# import theano
import theano
import theano.tensor as T
import theano.sparse
from theano.tensor.shared_randomstreams import RandomStreams
sys.path.extend(['/home/fujikawa/lib/python/other/pylearn2/pylearn2', '/home/fujikawa/StockPredict/src/deeplearning/dataset'])

# import my library
from XOR import XOR
from Nikkei import Nikkei

# activate_function = T.nnet.sigmoid
def activate_function(arg):
    return T.nnet.sigmoid(arg)
    # return T.nnet.sigmoid(arg) - T.cast(0.5, dtype=theano.config.floatX)

class RBM(object):
    """Restricted Boltzmann Machine (RBM)  """
    def __init__(self, input=None, n_visible=784, n_hidden=500, \
        W=None, hbias=None, vbias=None, numpy_rng=None,
        theano_rng=None, params=None, reg_weight=0):
        """
        RBM constructor. Defines the parameters of the model along with
        basic operations for inferring hidden from visible (and vice-versa),
        as well as for performing CD updates.

        :param input: None for standalone RBMs or symbolic variable if RBM is
        part of a larger graph.

        :param n_visible: number of visible units

        :param n_hidden: number of hidden units

        :param W: None for standalone RBMs or symbolic variable pointing to a
        shared weight matrix in case RBM is part of a DBN network; in a DBN,
        the weights are shared between RBMs and layers of a MLP

        :param hbias: None for standalone RBMs or symbolic variable pointing
        to a shared hidden units bias vector in case RBM is part of a
        different network

        :param vbias: None for standalone RBMs or a symbolic variable
        pointing to a shared visible units bias
        """

        numpy_rng = numpy.random.RandomState(123)
        theano_rng = RandomStreams(numpy_rng.randint(2 ** 30))
        self.reg_weight = reg_weight
        if params != None:
            if 'beta' in params:
                self.reg_weight = params['beta']
            W = theano.shared(params['W'], name='W', borrow=True)
            hbias = theano.shared(params['hbias'], name='hbias', borrow=True)
            vbias = theano.shared(params['vbias'], name='vbias', borrow=True)
            self.n_visible = params['n_visible']
            self.n_hidden = params['n_hidden']
            self.epoch = params['epoch']
            # theano_rng = params['theano_rng']

        else:

            self.n_visible = n_visible
            self.n_hidden = n_hidden
            self.epoch = 0
        print self.reg_weight
        if numpy_rng is None:
            # create a number generator
            numpy_rng = numpy.random.RandomState(1234)

        if theano_rng is None:
            theano_rng = RandomStreams(numpy_rng.randint(2 ** 30))

        if W is None:
            # W is initialized with `initial_W` which is uniformely
            # sampled from -4*sqrt(6./(n_visible+n_hidden)) and
            # 4*sqrt(6./(n_hidden+n_visible)) the output of uniform if
            # converted using asarray to dtype theano.config.floatX so
            # that the code is runable on GPU
            initial_W = numpy.asarray(numpy_rng.uniform(
                      # low=-4 * numpy.sqrt(6. / (n_hidden + n_visible)),
                      low=0,
                      high=8 * numpy.sqrt(6. / (n_hidden + n_visible)),
                      size=(n_visible, n_hidden)),
                      dtype=theano.config.floatX)
            # theano shared variables for weights and biases
            W = theano.shared(value=initial_W, name='W', borrow=True)
        if hbias is None:
            # create shared variable for hidden units bias
            hbias = theano.shared(value=numpy.zeros(n_hidden,
                                                    dtype=theano.config.floatX),
                                  name='hbias', borrow=True)

        if vbias is None:
            # create shared variable for visible units bias
            vbias = theano.shared(value=numpy.zeros(n_visible,
                                                    dtype=theano.config.floatX),
                                  name='vbias', borrow=True)

        # initialize input layer for standalone RBM or layer0 of DBN
        self.input = input
        if not input:
            self.input = T.matrix('input')

        self.W = W
        self.hbias = hbias
        self.vbias = vbias
        self.theano_rng = theano_rng
        # **** WARNING: It is not a good idea to put things in this list
        # other than shared variables created in this function.
        self.params = [self.W, self.hbias, self.vbias]
        
        matrix = T.matrix()
        vector = T.vector()        
        self.get_propup_vector = theano.function([vector], self.propup(vector)[1])
        self.get_propup_matrix = theano.function([matrix], self.propup(matrix)[1])
        matrix_maxpool = T.matrix()
        self.get_maxpool = theano.function([matrix_maxpool], T.max(self.propup(matrix_maxpool)[1], axis=0))
        # self.get_maxpool = numpy.max(self.get_propup_matrix, axis=0)

    def free_energy(self, v_sample):
        ''' Function to compute the free energy '''
        wx_b = T.dot(v_sample, self.W) + self.hbias
        vbias_term = T.dot(v_sample, self.vbias)
        hidden_term = T.sum(T.log(1 + T.exp(wx_b)), axis=1)
        return -hidden_term - vbias_term

    def propup(self, vis):
        '''This function propagates the visible units activation upwards to
        the hidden units

        Note that we return also the pre-sigmoid activation of the
        layer. As it will turn out later, due to how Theano deals with
        optimizations, this symbolic variable will be needed to write
        down a more stable computational graph (see details in the
        reconstruction cost function)

        '''
        pre_sigmoid_activation = T.dot(vis, self.W) + self.hbias
        return [pre_sigmoid_activation, activate_function(pre_sigmoid_activation)]

    def sample_h_given_v(self, v0_sample):
        ''' This function infers state of hidden units given visible units '''
        # compute the activation of the hidden units given a sample of
        # the visibles
        pre_sigmoid_h1, h1_mean = self.propup(v0_sample)
        # get a sample of the hiddens given their activation
        # Note that theano_rng.binomial returns a symbolic sample of dtype
        # int64 by default. If we want to keep our computations in floatX
        # for the GPU we need to specify to return the dtype floatX
        h1_sample = self.theano_rng.binomial(size=h1_mean.shape,
                                             n=1, p=h1_mean,
                                             dtype=theano.config.floatX)
        return [pre_sigmoid_h1, h1_mean, h1_sample]

    def propdown(self, hid):
        '''This function propagates the hidden units activation downwards to
        the visible units

        Note that we return also the pre_sigmoid_activation of the
        layer. As it will turn out later, due to how Theano deals with
        optimizations, this symbolic variable will be needed to write
        down a more stable computational graph (see details in the
        reconstruction cost function)

        '''
        pre_sigmoid_activation = T.dot(hid, self.W.T) + self.vbias
        return [pre_sigmoid_activation, activate_function(pre_sigmoid_activation)]

    def sample_v_given_h(self, h0_sample):
        ''' This function infers state of visible units given hidden units '''
        # compute the activation of the visible given the hidden sample
        pre_sigmoid_v1, v1_mean = self.propdown(h0_sample)
        # get a sample of the visible given their activation
        # Note that theano_rng.binomial returns a symbolic sample of dtype
        # int64 by default. If we want to keep our computations in floatX
        # for the GPU we need to specify to return the dtype floatX
        v1_sample = self.theano_rng.binomial(size=v1_mean.shape,
                                             n=1, p=v1_mean,
                                             dtype=theano.config.floatX)
        return [pre_sigmoid_v1, v1_mean, v1_sample]

    def gibbs_hvh(self, h0_sample):
        ''' This function implements one step of Gibbs sampling,
            starting from the hidden state'''
        pre_sigmoid_v1, v1_mean, v1_sample = self.sample_v_given_h(h0_sample)
        pre_sigmoid_h1, h1_mean, h1_sample = self.sample_h_given_v(v1_sample)
        return [pre_sigmoid_v1, v1_mean, v1_sample,
                pre_sigmoid_h1, h1_mean, h1_sample]

    def gibbs_vhv(self, v0_sample):
        ''' This function implements one step of Gibbs sampling,
            starting from the visible state'''
        pre_sigmoid_h1, h1_mean, h1_sample = self.sample_h_given_v(v0_sample)
        pre_sigmoid_v1, v1_mean, v1_sample = self.sample_v_given_h(h1_sample)
        return [pre_sigmoid_h1, h1_mean, h1_sample,
                pre_sigmoid_v1, v1_mean, v1_sample]

    def get_cost_updates(self, lr=0.1, persistent=None, k=1):
        """This functions implements one step of CD-k or PCD-k

        :param lr: learning rate used to train the RBM

        :param persistent: None for CD. For PCD, shared variable
            containing old state of Gibbs chain. This must be a shared
            variable of size (batch size, number of hidden units).

        :param k: number of Gibbs steps to do in CD-k/PCD-k

        Returns a proxy for the cost and the updates dictionary. The
        dictionary contains the update rules for weights and biases but
        also an update of the shared variable used to store the persistent
        chain, if one is used.

        """
        # return theano.shared([[1,1,1], [1,1]]), [theano.shared([[1,1,1], [1,1]]),theano.shared([[1,1,1], [1,1]])] 

        # compute positive phase
        pre_sigmoid_ph, ph_mean, ph_sample = self.sample_h_given_v(self.input)

        # decide how to initialize persistent chain:
        # for CD, we use the newly generate hidden sample
        # for PCD, we initialize from the old state of the chain
        if persistent is None:
            chain_start = ph_sample
        else:
            chain_start = persistent

        # perform actual negative phase
        # in order to implement CD-k/PCD-k we need to scan over the
        # function that implements one gibbs step k times.
        # Read Theano tutorial on scan for more information :
        # http://deeplearning.net/software/theano/library/scan.html
        # the scan will return the entire Gibbs chain
        [pre_sigmoid_nvs, nv_means, nv_samples,
         pre_sigmoid_nhs, nh_means, nh_samples], updates = \
            theano.scan(self.gibbs_hvh,
                    # the None are place holders, saying that
                    # chain_start is the initial state corresponding to the
                    # 6th output
                    outputs_info=[None,  None,  None, None, None, chain_start],
                    n_steps=k)

        # determine gradients on RBM parameters
        # not that we only need the sample at the end of the chain
        chain_end = nv_samples[-1]

        l2_w, l2_h = self.get_norm_penalty(self.input, isUpdate=True)
        cost = T.mean(self.free_energy(self.input)) - T.mean(self.free_energy(chain_end))
        # cost += self.reg_weight * 0.1 * T.sum(T.mean(activate_function(T.dot(self.input, self.W) + self.hbias), axis=0))
        # cost += self.reg_weight * T.sum(T.mean(activate_function(T.dot(self.input, self.W) + self.hbias), axis=0) ** 2)
        # cost += self.reg_weight * 0.5 * T.sum(T.mean(self.propup(self.input)[1], axis=0))
        cost += l2_w
        cost += l2_h
        # cost += 0.001 * self.reg_weight * T.sum((1 - T.max(self.propup(self.input)[1], axis=0)) ** 2)
        # cost += self.reg_weight * cross_entropy(5e-3, T.mean(T.mean(self.sample_h_given_v(self.input)[2], axis=0)))
        # cost += l2() 
        # cost += KL(0.02, T.mean(ph_mean))
        # We must not compute the gradient through the gibbs sampling
        gparams = T.grad(cost, self.params, consider_constant=[chain_end])

        # constructs the update dictionary
        i = 0
        for gparam, param in zip(gparams, self.params):
            if i == 0:
            # # make sure that the learning rate is of the right dtype
            #     param_fixed = param - gparam * T.cast(lr, dtype=theano.config.floatX)
            #     param_fixed = (param_fixed + abs(param_fixed)) / 2
            #     updates[param] = param_fixed
                param_fixed = param - gparam * T.cast(lr, dtype=theano.config.floatX)
                param_fixed = (param_fixed - param_fixed.min(axis=0)) ** 2
                param_fixed /= (param_fixed.max(axis=0) + 0.001)
                updates[param] = param_fixed
            else:
                updates[param] = param - gparam * T.cast(lr, dtype=theano.config.floatX)
            i += 1
        if persistent:
            # Note that this works only if persistent is a shared variable
            updates[persistent] = nh_samples[-1]
            # pseudo-likelihood is a better proxy for PCD
            monitoring_cost = self.get_pseudo_likelihood_cost(updates)
        else:
            # reconstruction cross-entropy is a better proxy for CD
            monitoring_cost = self.get_reconstruction_cost(updates,
                                                           pre_sigmoid_nvs[-1])

        return monitoring_cost, updates
    
    def get_norm_penalty(self, x, isUpdate=True):

        def l1(param):
            return T.sum(T.abs(param))
        def l2(param):
            return T.sum(param ** 2)
        def l2_a0(param):
            return T.sum(param ** 2)
        def KL(p, p_hat):
            return T.sum((p * T.log(p / p_hat)) + ((1 - p) * T.log((1 - p) / (1 - p_hat))))

        # l1_w = l1(self.W)
        l2_w = self.reg_weight * l2(self.W)
        l2_h = 0
        # l1_h = l1(self.get_propup_matrix(x))
        # if isUpdate == True:
        #     l2_h = 0
        #     # l2_h = self.reg_weight * l2(self.propup(x)[1])
        # else:
        #     l2_h = 0
        #     # l2_h = 0 * self.reg_weight * l2(self.get_propup_matrix(x))
        
        return l2_w, l2_h

        # l1_h = 
    def get_pseudo_likelihood_cost(self, updates):
        """Stochastic approximation to the pseudo-likelihood"""

        # index of bit i in expression p(x_i | x_{\i})
        bit_i_idx = theano.shared(value=0, name='bit_i_idx')

        # binarize the input image by rounding to nearest integer
        xi = T.round(self.input)

        # calculate free energy for the given bit configuration
        fe_xi = self.free_energy(xi)

        # flip bit x_i of matrix xi and preserve all other bits x_{\i}
        # Equivalent to xi[:,bit_i_idx] = 1-xi[:, bit_i_idx], but assigns
        # the result to xi_flip, instead of working in place on xi.
        xi_flip = T.set_subtensor(xi[:, bit_i_idx], 1 - xi[:, bit_i_idx])

        # calculate free energy with bit flipped
        fe_xi_flip = self.free_energy(xi_flip)

        # equivalent to e^(-FE(x_i)) / (e^(-FE(x_i)) + e^(-FE(x_{\i})))
        cost = T.mean(self.n_visible * T.log(activate_function(fe_xi_flip -
                                                            fe_xi)))

        # increment bit_i_idx % number as part of updates
        updates[bit_i_idx] = (bit_i_idx + 1) % self.n_visible

        return cost

    def get_reconstruction_cost(self, updates, pre_sigmoid_nv):
        """Approximation to the reconstruction error

        Note that this function requires the pre-sigmoid activation as
        input.  To understand why this is so you need to understand a
        bit about how Theano works. Whenever you compile a Theano
        function, the computational graph that you pass as input gets
        optimized for speed and stability.  This is done by changing
        several parts of the subgraphs with others.  One such
        optimization expresses terms of the form log(sigmoid(x)) in
        terms of softplus.  We need this optimization for the
        cross-entropy since sigmoid of numbers larger than 30. (or
        even less then that) turn to 1. and numbers smaller than
        -30. turn to 0 which in terms will force theano to compute
        log(0) and therefore we will get either -inf or NaN as
        cost. If the value is expressed in terms of softplus we do not
        get this undesirable behaviour. This optimization usually
        works fine, but here we have a special case. The sigmoid is
        applied inside the scan op, while the log is
        outside. Therefore Theano will only see log(scan(..)) instead
        of log(sigmoid(..)) and will not apply the wanted
        optimization. We can not go and replace the sigmoid in scan
        with something else also, because this only needs to be done
        on the last step. Therefore the easiest and more efficient way
        is to get also the pre-sigmoid activation as an output of
        scan, and apply both the log and sigmoid outside scan such
        that Theano can catch and optimize the expression.

        """        

        cross_entropy = T.mean(
                T.sum(self.input * T.log(activate_function(pre_sigmoid_nv)) +
                (1 - self.input) * T.log(1 - activate_function(pre_sigmoid_nv)),
                      axis=1))


        return cross_entropy
    def output_params(self):
        W = numpy.asarray(self.W.get_value())
        hbias = numpy.asarray(self.hbias.get_value())
        vbias = numpy.asarray(self.vbias.get_value())
        # theano_rng = numpy.asarray(self.theano_rng, dtype=numpy.float64)
        # pdb.set_trace()
        params = {
            'W' : W,
            'hbias' : hbias,
            'vbias' : vbias,
            'n_visible' : self.n_visible,
            'n_hidden' : self.n_hidden,
            'epoch' : self.epoch,
            'beta' : self.reg_weight
            # 'theano_rng' : self.theano_rng
        }
        return params


def train_rbm(input=None, model=None, dataset=None, learning_rate=1e-2, training_epochs=15, batch_size=200,
             n_chains=1, n_samples=10, outdir='', k=1):
    """
    Demonstrate how to train and afterwards sample from it using Theano.

    This is demonstrated on MNIST.

    :param learning_rate: learning rate used for training the RBM

    :param training_epochs: number of epochs used for training

    :param dataset: path the the pickled dataset

    :param batch_size: size of a batch used to train the RBM

    :param n_chains: number of parallel Gibbs chains to be used for sampling

    :param n_samples: number of samples to plot for each chain

    """
    print 'start to train RBM'
    # datasets = XOR()
    if dataset == None:
        print 'dataset is not provided'
        sys.exit()
    

    
    
    model.k = k
    # compute number of minibatches for training, validation and testing
    # n_train_batches = datasets.get_train_data(type='theano_dense').get_value(borrow=True).shape[0] / batch_size
    n_train_batches = dataset.phase1['train'].get_value(borrow=True).shape[0] / batch_size
    print n_train_batches
    # allocate symbolic variables for the data

    # index = theano.sparse.basic.GetItemScalar(index)
    x = input  # the data is presented as rasterized images

    rng = numpy.random.RandomState(123)
    theano_rng = RandomStreams(rng.randint(2 ** 30))

    # initialize storage for the persistent chain (state = hidden
    # layer of chain)
    persistent_chain = theano.shared(numpy.zeros((batch_size, model.n_hidden),
                                                 dtype=theano.config.floatX),
                                     borrow=True)

    # get the cost and the gradient corresponding to one step of CD-15
    cost, updates = model.get_cost_updates(lr=learning_rate,
                                         persistent=None, k=k)

    #################################
    #     Training the RBM          #
    #################################
    


    # print type(index)
    # print type(datasets.get_theano_sparse_design())
    # print type(datasets.get_theano_design())

    # sys.exit()
    i = 0
    index = T.lscalar()    # index to a [mini]batch
    

    # print T.TensorType('float64', datasets.get_train_data(type='theano_sparse')[index: index+1])
    # pdb.set_trace()

    # it is ok for a theano function to have no output
    # the purpose of train_rbm is solely to update the RBM parameters

    trainer = theano.function([index], cost,
           updates=updates,
           # givens={x: theano.sparse.dense_from_sparse( datasets.get_train_data(type='theano_sparse')[index * batch_size:  (index + 1) * batch_size])},
           
           givens={x: dataset.get_batch_design(index, batch_size, dataset.phase1['train'])},
           #givens={x: datasets.get_theano_sparse_design()[i * batch_size:  (i + 1) * batch_size]},
           name='train_rbm')

    plotting_time = 0.
    start_time = time.clock()

    x_example = dataset.get_batch_design(0, 2500, dataset.phase1['valid']).eval()
    # pdb.set_trace()

    # l2_w, l2_h = model.get_norm_penalty(x_example, isUpdate=False)
    print outdir.split('/')[len(outdir.split('/')) - 1]

    for epoch in xrange(training_epochs):

        # go through the training set
        mean_cost = []
        previous_cost = 0
        for batch_index in xrange(n_train_batches):
            
            while(True):
                try:
                    mean_cost += [trainer(batch_index)]
                    msg = '%s e: %d, b: %d, c: %.2f, '% (str(datetime.datetime.now().strftime("%m/%d %H:%M")), epoch, batch_index, numpy.mean(mean_cost))
                    if batch_index % 30 == 0:
                        l2_w, l2_h = model.get_norm_penalty(x_example, isUpdate=False)
                        test_propup = model.get_propup_matrix(x_example)
                        # msg += 'l2_w: %.2f, l2_h: %.2f, ' % (float(l2_w.eval()), float(l2_h.eval()))
                        msg += 'l2_w: %.2f, ' % (float(l2_w.eval()))
                        msg += 'sp: %.2f, mm: %.2f~%.2f, ' % (test_propup.mean(axis=1).mean(), test_propup.max(axis=0).mean(), test_propup.min(axis=0).mean())
                        msg += '%s' % str(numpy.histogram(test_propup.mean(axis=0), range=[0,1])[0])
                        # pdb.set_trace()
                        # print msg

                    sys.stdout.write("\r%s" % msg)
                    sys.stdout.flush()
                    break
                except KeyboardInterrupt:
                    pdb.set_trace()

                
            previous_cost = numpy.mean(mean_cost)
        model.epoch += 1
        params = model.output_params()
        print
        print outdir.split('/')[len(outdir.split('/')) - 1]
        while(True):
            try:
                f_out = open(outdir, 'w')
                f_out.write(cPickle.dumps(params, 1))
                f_out.close()
                break
            except:
                print 'File could not be written...'
                pdb.set_trace()
        # f_out = open(outdir, 'w')
        # f_out.write(cPickle.dumps(model))
        # f_out.close()
        # cPickle.dumps(model)
        # print 'Training epoch %d, cost is ' % epoch, numpy.mean(mean_cost)

    while(True):
        try:
            f_out = open(outdir, 'w')
            f_out.write(cPickle.dumps(params, 1))
            f_out.close()
            break
        except:
            print 'File could not be written...'
            pdb.set_trace()
    
    end_time = time.clock()

    pretraining_time = (end_time - start_time) - plotting_time

    print ('Training took %f minutes' % (pretraining_time / 60.))

    
if __name__ == '__main__':
    dataset = StockPrice()
    train_rbm(dataset=dataset,learning_rate=0.1, n_hidden=100)


