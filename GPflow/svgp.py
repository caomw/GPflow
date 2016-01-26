import tensorflow as tf
import numpy as np
from param import Param
from .model import GPModel
import transforms
import conditionals
from .mean_functions import Zero
from tf_hacks import eye


class SVGP(GPModel):
    """
    This is the Sparse Variational GP (SVGP). The key reference is

    @inproceedings{hensman2014scalable,
      title={Scalable Variational Gaussian Process Classification},
      author={Hensman, James and Matthews, Alexander G. de G. and Ghahramani, Zoubin},
      booktitle={Proceedings of AISTATS},
      year={2015}
    }

    """
    def __init__(self, X, Y, kern, likelihood, Z, mean_function=Zero(), num_latent=None, q_diag=False, whiten=True):
        """
        X is a data matrix, size N x D
        Y is a data matrix, size N x R
        kern, likelihood, mean_function are appropriate GPflow objects
        Z is a matrix of pseudo inputs, size M x D
        num_latent is the number of latent process to use, default to Y.shape[1]
        q_diag is a boolean. If True, the covariance is approximated by a diagonal matrix.
        whiten is a boolean. It True, we use the whitened represenation of the inducing points.
        """
        GPModel.__init__(self, X, Y, kern, likelihood, mean_function)
        self.q_diag, self.whiten = q_diag, whiten
        self.Z = Param(Z)
        self.num_latent = num_latent or Y.shape[1]
        self.num_inducing = Z.shape[0]

        self.q_mu = Param(np.zeros((self.num_inducing, self.num_latent)))
        if self.q_diag:
            self.q_sqrt = Param(np.ones((self.num_inducing, self.num_latent)), transforms.positive)
        else:
            self.q_sqrt = Param(np.array([np.eye(self.num_inducing) for _ in range(self.num_latent)]).swapaxes(0,2))

    def build_prior_KL(self):
        
        if self.whiten:
            #First compute KL[q(v) || p(v)]] for each column of v
            KL = 0.5*tf.reduce_sum(tf.square(self.q_mu)) #Mahalanobis term
            KL -= 0.5*self.num_inducing*self.num_latent #Constant term.
            if self.q_diag:
                KL += -0.5*tf.reduce_sum(tf.log(tf.square(self.q_sqrt))) + 0.5*tf.reduce_sum(tf.square(self.q_sqrt))
            else:
                #here we loop through all the independent functions, extracting the triangular part. 
                for d in range(self.num_latent):
                    Lq = tf.user_ops.triangle(self.q_sqrt[:,:,d], 'lower')
                    KL -= 0.5*tf.reduce_sum(tf.log(tf.square(tf.user_ops.get_diag(Lq)))) #Log determinant of q covariance.
                    KL +=  0.5*tf.reduce_sum(tf.square(Lq))  #Trace term.
        else:
            L = tf.cholesky(self.kern.K(self.Z) + eye(self.num_inducing) * 1e-6)
            alpha = tf.user_ops.triangular_solve(L, self.q_mu, 'lower')
            KL = 0.5*tf.reduce_sum(tf.square(alpha)) #Mahalanobis term.
            KL += self.num_latent * 0.5*tf.reduce_sum(tf.log(tf.square(tf.user_ops.get_diag(L) ))) #Prior log determinant term.
            KL -= 0.5*self.num_inducing*self.num_latent
            if self.q_diag:
                L_inv = tf.user_ops.triangular_solve(L, eye(self.num_inducing), 'lower')
                K_inv = tf.user_ops.triangular_solve(tf.transpose(L), L_inv, 'upper')
                KL -= 0.5*tf.reduce_sum(tf.log(tf.square(self.q_sqrt))) #Log determinant of q covariance. 
                KL += 0.5 * tf.reduce_sum(tf.expand_dims(tf.user_ops.get_diag(K_inv), 1) * tf.square(self.q_sqrt)) #Trace term.
            else:
                for d in range(self.num_latent):
                    Lq = tf.user_ops.triangle(self.q_sqrt[:,:,d], 'lower')
                    KL+= -0.5*tf.reduce_sum(tf.log(tf.square(tf.user_ops.get_diag(Lq)))) #Log determinant of q covariance. 
                    LiLq = tf.user_ops.triangular_solve(L, Lq, 'lower')
                    KL += 0.5*tf.reduce_sum(tf.square(LiLq)) #Trace term
        
        return KL


    def build_likelihood(self):
        """
        This gives a variational bound on the model likelihood.

        There are four possible cases here, all combinations of True/False for self.whiten and self.q_diag.
        """

        #Get prior KL.
        KL  = self.build_prior_KL()
    
        #Get conditionals
        if self.whiten:
            fmean, fvar = conditionals.gaussian_gp_predict_whitened(self.X, self.Z, self.kern, self.q_mu, self.q_sqrt, self.num_latent)
        else:
            fmean, fvar = conditionals.gaussian_gp_predict(self.X, self.Z, self.kern, self.q_mu, self.q_sqrt, self.num_latent)            

        #add in mean function to conditionals.
        fmean += self.mean_function(self.X)
        
        #Get variational expectations.
        variational_expectations = self.likelihood.variational_expectations(fmean, fvar, self.Y)
        
        return tf.reduce_sum(variational_expectations) - KL

    def build_predict(self, Xnew):
        if self.whiten:
            mu, var =  conditionals.gaussian_gp_predict_whitened(Xnew, self.Z, self.kern, self.q_mu, self.q_sqrt, self.num_latent)
        else:
            mu, var =  conditionals.gaussian_gp_predict(Xnew, self.Z, self.kern, self.q_mu, self.q_sqrt, self.num_latent)
        return mu + self.mean_function(Xnew), var


      


