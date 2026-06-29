"""数据验证 (Validation)

数据完整性和一致性验证。

职责：
- 校验采集数据的完整性
- 评估各维度数据置信度
- 检测数据异常和不一致
"""

from .integrity_checker import IntegrityDataValidator, check_data_integrity

__all__ = ["check_data_integrity", "IntegrityDataValidator"]
