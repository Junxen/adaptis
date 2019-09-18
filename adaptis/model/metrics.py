import mxnet as mx
import numpy as np
import sklearn.metrics


class TrainMetric(object):
    def __init__(self, pred_outputs, gt_outputs):
        self.pred_outputs = pred_outputs
        self.gt_outputs = gt_outputs

    def update(self, *args, **kwargs):
        raise NotImplementedError

    def get_epoch_value(self):
        raise NotImplementedError

    def reset_epoch_stats(self):
        raise NotImplementedError

    def log_states(self, sw, tag_prefix, global_step):
        pass

    @property
    def name(self):
        return type(self).__name__


class AdaptiveIoU(TrainMetric):
    def __init__(self, init_thresh=0.4, thresh_step=0.025, thresh_beta=0.99, iou_beta=0.9,
                 ignore_label=-1, from_logits=True,
                 pred_output='instances', gt_output='instances'):
        super().__init__(pred_outputs=(pred_output,), gt_outputs=(gt_output,))
        self._ignore_label = ignore_label
        self._from_logits = from_logits
        self._iou_thresh = init_thresh
        self._thresh_step = thresh_step
        self._thresh_beta = thresh_beta
        self._iou_beta = iou_beta
        self._ema_iou = 0.0
        self._epoch_iou_sum = 0.0
        self._epoch_batch_count = 0

    def update(self, pred, gt):
        gt_mask = gt > 0
        if self._from_logits:
            pred = mx.nd.sigmoid(pred)

        gt_mask_area = mx.nd.sum(gt_mask, axis=0, exclude=True).asnumpy()
        if np.all(gt_mask_area == 0):
            return

        ignore_mask = gt == self._ignore_label
        max_iou = _compute_iou(pred > self._iou_thresh, gt_mask, ignore_mask).mean()
        best_thresh = self._iou_thresh
        for t in [best_thresh - self._thresh_step, best_thresh + self._thresh_step]:
            temp_iou = _compute_iou(pred > t, gt_mask, ignore_mask).mean()
            if temp_iou > max_iou:
                max_iou = temp_iou
                best_thresh = t

        self._iou_thresh = self._thresh_beta * self._iou_thresh + (1 - self._thresh_beta) * best_thresh
        self._ema_iou = self._iou_beta * self._ema_iou + (1 - self._iou_beta) * max_iou
        self._epoch_iou_sum += max_iou
        self._epoch_batch_count += 1

    def get_epoch_value(self):
        if self._epoch_batch_count > 0:
            return self._epoch_iou_sum / self._epoch_batch_count
        else:
            return 0.0

    def reset_epoch_stats(self):
        self._epoch_iou_sum = 0.0
        self._epoch_batch_count = 0

    def log_states(self, sw, tag_prefix, global_step):
        sw.add_scalar(tag=tag_prefix + '_ema_iou', value=self._ema_iou, global_step=global_step)
        sw.add_scalar(tag=tag_prefix + '_iou_thresh', value=self._iou_thresh, global_step=global_step)

    @property
    def iou_thresh(self):
        return self._iou_thresh


class AUC(TrainMetric):
    def __init__(self, beta=0.9, ignore_label=-1, from_logits=True,
                 pred_output='instances', gt_output='instances'):
        super().__init__(pred_outputs=(pred_output,), gt_outputs=(gt_output,))
        self._ignore_label = ignore_label
        self._from_logits = from_logits
        self._beta = beta
        self._ema_auc = 0.0
        self._epoch_auc_sum = 0.0
        self._epoch_batch_count = 0

    def update(self, pred, gt):
        gt_mask = gt > 0
        if self._from_logits:
            pred = mx.nd.sigmoid(pred)

        gt_mask_area = mx.nd.sum(gt_mask, axis=0, exclude=True).asnumpy()
        if np.all(gt_mask_area == 0):
            return

        ignore_mask = gt == self._ignore_label
        auc = _compute_auc(pred, gt_mask, ignore_mask).mean()
        self._ema_auc = self._beta * self._ema_auc + (1 - self._beta) * auc
        self._epoch_auc_sum += auc
        self._epoch_batch_count += 1

    def get_epoch_value(self):
        if self._epoch_batch_count > 0:
            return self._epoch_auc_sum / self._epoch_batch_count
        else:
            return 0.0

    def reset_epoch_stats(self):
        self._epoch_auc_sum = 0.0
        self._epoch_batch_count = 0

    def log_states(self, sw, tag_prefix, global_step):
        sw.add_scalar(tag=tag_prefix + '_ema', value=self._ema_auc, global_step=global_step)


def _compute_iou(pred_mask, gt_mask, ignore_mask, keep_ignore=False):
    pred_mask = mx.nd.where(ignore_mask, mx.nd.zeros_like(pred_mask), pred_mask)
    union = mx.nd.mean(mx.nd.logical_or(pred_mask, gt_mask), axis=0, exclude=True).asnumpy()
    intersection = mx.nd.mean(mx.nd.logical_and(pred_mask, gt_mask), axis=0, exclude=True).asnumpy()
    nonzero = union > 0

    iou = intersection[nonzero] / union[nonzero]
    if not keep_ignore:
        return iou
    else:
        result = np.full_like(intersection, -1)
        result[nonzero] = iou
        return result


def _compute_auc(pred, gt_mask, ignore_mask):
    pred = mx.nd.where(ignore_mask, mx.nd.zeros_like(pred), pred).asnumpy()
    gt_mask = gt_mask.asnumpy()

    samples_auc = []
    for i in range(pred.shape[0]):
        sample_pred = pred[i]
        sample_gt = gt_mask[i]
        if sample_gt.sum() == 0:
            continue

        precision, recall, _ = sklearn.metrics.precision_recall_curve(sample_gt.flatten(), sample_pred.flatten())
        auc = sklearn.metrics.auc(recall, precision)
        samples_auc.append(auc)

    return np.array(samples_auc)
