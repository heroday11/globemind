import numpy as np
from datetime import datetime

def compute_joint_distance(vec_a, vec_b, ts_a, ts_b):
    """
    计算结合了语义和时间的联合距离。
    如果时间跨度 > 14 天，直接返回 999.0 (强拦截)
    """
    delta_days = abs(ts_a - ts_b) / 86400
    if delta_days > 14:
        return 999.0
    
    # 基础语义距离 (余弦距离)
    cosine_dist = 1.0 - np.dot(vec_a, vec_b) / (np.linalg.norm(vec_a) * np.linalg.norm(vec_b))
    
    # 时间衰减惩罚 (14天内的轻微惩罚)
    time_penalty = 1.0 + (delta_days / 14.0) * 0.2
    return cosine_dist * time_penalty
