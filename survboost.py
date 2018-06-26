import numpy as np
import torch
from scoring_rules import MLE_surv, CRPS_surv
from base_models import Base_Linear
from torch.distributions.log_normal import LogNormal, Normal
from torch.distributions import Exponential
from torch.distributions.constraint_registry import transform_to
from sklearn.tree import DecisionTreeRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

from evaluation import calculate_concordance_naive


class SurvBoost(object):
    def __init__(self, Dist=LogNormal, Score=MLE_surv, Base=Base_Linear, n_estimators=1000, learning_rate=1, minibatch_frac=1.0):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.minibatch_frac = minibatch_frac
        self.Dist = Dist
        self.D = lambda args: Dist(*[transform_to(constraint)(arg) for (param, constraint), arg in zip(Dist.arg_constraints.items(), args)])
        self.Score = Score
        self.Base = Base
        self.base_models = []

    def init_params(self, size):
        Z = lambda l: np.zeros(size).astype(np.float32)
        return [Z(size) for _ in  self.Dist.arg_constraints]

    def pred_param(self, X):
        m, n = X.shape
        params = self.init_params(m)

        for models in self.base_models:
            base_params = [model.predict(X) for model in models]
            params = [p - self.learning_rate * b for p, b in zip(params, base_params)]
        
        return [torch.tensor(p, requires_grad=True, dtype=torch.float32) for p in params]

    def sample(self, X, Y, C):
        sample_size = int(self.minibatch_frac * len(Y))
        idxs = np.random.choice(np.arange(len(Y)), sample_size, replace=False)
        return X[idxs,:], torch.Tensor(Y[idxs]), torch.Tensor(C[idxs])

    def fit_base(self, X, resids):
        models = [self.Base().fit(X, rs) for rs in resids]
        self.base_models.append(models)

    def mul(self, array, num):
        return [a * num for a in array]

    def sub(self, A, B):
        return [a - b for a, b in zip(A, B)]

    def norm(self, grads):
        # of course this is not the norm, but serves the purpose
        return sum([float(torch.norm(g)) for g in grads])

    def line_search(self, fn, start, grad):
        loss_init = fn(start)
        scale = 1.
        while float(fn(self.sub(start, self.mul(grad, scale)))) > float(loss_init):
            scale = scale / 2.
        return scale

    def fit(self, X, Y, C):
        for itr in range(self.n_estimators):
            X_batch, Y_batch, C_batch = self.sample(X, Y, C)

            params = self.pred_param(X_batch)

            Forecast = self.D(params)

            score = self.Score(Forecast, Y_batch, C_batch).mean()
            print('[iter %d] loss=%f' % (itr, float(score)))
            if float(score) == float('-inf'):
                break
            if str(float(score)) == 'nan':
                print(params)
                print(self.Score(Forecast, Y_batch, C_batch))
                for (m, s, y, c) in zip(params[0], params[1], Y_batch, C_batch):
                    ln = LogNormal(m, s.exp())
                    print(float(m), float(s), float(y), float(c), float(ln.log_prob(y)), float(ln.cdf(y)))
                break

            score.backward()

            grads = [p.grad for p in params]

            scale = self.line_search(lambda p_: self.Score(self.D(p_), Y_batch, C_batch).mean(), params, grads)

            resids = self.mul(grads, scale)
            if self.norm(resids) < 1e-5:
                break

            self.fit_base(X_batch, self.mul(resids, scale))

    def pred_dist(self, X):
        params = self.pred_param(X)
        dist = self.D(params)
        return dist

    def pred_mean(self, X):
        dist = self.pred_dist(X)
        return dist.mean.data.numpy()
        
    def pred_median(self, X):
        dist = self.pred_dist(X)
        return dist.icdf(torch.tensor(0.5)).data.numpy()

def main():
    m, n = 100, 50
    X = np.random.rand(m, n).astype(np.float32)
    Y = np.random.rand(m).astype(np.float32) * 2 + 1
    C = (np.random.rand(m) > 0.5).astype(np.float32)

    sb = SurvBoost(Base = lambda : DecisionTreeRegressor(criterion='mse'),
                   Dist = LogNormal,
                   Score = CRPS_surv,
                   n_estimators = 200)
    sb.fit(X, Y, C)
    preds_dt = sb.pred_mean(X)
    # print(sb.pred_mean(X))
    # print(Y)
    # print(C)
    
#     sb = SurvBoost(Base = LinearRegression, n_estimators = 1000)
#     sb.fit(X, Y, C)
#     preds_lin = sb.pred_mean(X)

    print("Train/DecTree:", calculate_concordance_naive(preds_dt, Y, C))
    print('Pred_mean: %f, True_mean: %f' % (np.mean(preds_dt), np.mean(Y)))
#    print("Train/LinReg:", calculate_concordance_naive(preds_lin, Y ,C))

    # X_test = np.random.rand(m, n).astype(np.float32)
    # Y_pred = sb.pred_mean(X_test)


if __name__ == '__main__':
    main()
