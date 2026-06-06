"""BaseModel - 模型抽象基类。

定义模型的标准接口，支持可插拔的模型架构。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class BaseModel(ABC):
    """模型抽象基类 - 定义标准接口。

    所有模型（XGBoost, LightGBM, 神经网络等）都应实现此接口。
    """

    @abstractmethod
    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        eval_set: tuple[pd.DataFrame, pd.Series] | None = None,
        **kwargs: Any,
    ) -> None:
        """训练模型。

        Args:
            X: 特征矩阵
            y: 标签
            eval_set: 验证集（可选）
            **kwargs: 其他参数
        """
        pass

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """预测。

        Args:
            X: 特征矩阵

        Returns:
            预测结果数组
        """
        pass

    @abstractmethod
    def save(self, path: Path) -> None:
        """保存模型。

        Args:
            path: 保存路径
        """
        pass

    @classmethod
    @abstractmethod
    def load(cls, path: Path) -> BaseModel:
        """加载模型。

        Args:
            path: 模型路径

        Returns:
            加载的模型实例
        """
        pass

    @property
    def model_type(self) -> str:
        """模型类型标识。"""
        return self.__class__.__name__

    def get_params(self) -> dict[str, Any]:
        """获取模型参数。"""
        return {}


class XGBoostModel(BaseModel):
    """XGBoost 模型实现。"""

    def __init__(self, **kwargs: Any):
        import xgboost as xgb
        self.model: xgb.Booster | None = None
        self.params = kwargs

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        eval_set: tuple[pd.DataFrame, pd.Series] | None = None,
        **kwargs: Any,
    ) -> None:
        """训练 XGBoost 模型。"""
        import xgboost as xgb

        dtrain = xgb.DMatrix(X, label=y)
        deval = None
        if eval_set is not None:
            X_eval, y_eval = eval_set
            deval = xgb.DMatrix(X_eval, label=y_eval)

        self.model = xgb.train(
            self.params,
            dtrain,
            evals=[(deval, "val")] if deval else None,
            **kwargs,
        )

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """XGBoost 预测。"""
        if self.model is None:
            raise ValueError("Model not trained. Call fit() first.")
        import xgboost as xgb
        dmatrix = xgb.DMatrix(X)
        return self.model.predict(dmatrix)

    def save(self, path: Path) -> None:
        """保存 XGBoost 模型。"""
        if self.model is None:
            raise ValueError("No model to save")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.model.save_model(str(path))

    @classmethod
    def load(cls, path: Path) -> XGBoostModel:
        """加载 XGBoost 模型。"""
        import xgboost as xgb
        model = cls()
        model.model = xgb.Booster()
        model.model.load_model(str(path))
        return model


class LightGBMModel(BaseModel):
    """LightGBM 模型实现。"""

    def __init__(self, **kwargs: Any):
        import lightgbm as lgb
        self.model: lgb.Booster | None = None
        self.params = kwargs

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        eval_set: tuple[pd.DataFrame, pd.Series] | None = None,
        **kwargs: Any,
    ) -> None:
        """训练 LightGBM 模型。"""
        import lightgbm as lgb

        self.model = lgb.LGBMRegressor(**self.params)
        self.model.fit(X, y, eval_set=[eval_set] if eval_set else None, **kwargs)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """LightGBM 预测。"""
        if self.model is None:
            raise ValueError("Model not trained. Call fit() first.")
        return self.model.predict(X)

    def save(self, path: Path) -> None:
        """保存 LightGBM 模型。"""
        if self.model is None:
            raise ValueError("No model to save")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.model.save_model(str(path))

    @classmethod
    def load(cls, path: Path) -> LightGBMModel:
        """加载 LightGBM 模型。"""
        import lightgbm as lgb
        model = cls()
        model.model = lgb.Booster(model_file=str(path))
        return model


class EnsembleModel(BaseModel):
    """集成模型 - 支持多个子模型的加权平均。"""

    def __init__(self, models: list[BaseModel], weights: list[float] | None = None):
        self.models = models
        self.weights = weights or [1.0 / len(models)] * len(models)

        if len(self.weights) != len(self.models):
            raise ValueError("Number of weights must match number of models")

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        eval_set: tuple[pd.DataFrame, pd.Series] | None = None,
        **kwargs: Any,
    ) -> None:
        """训练所有子模型。"""
        for model in self.models:
            model.fit(X, y, eval_set=eval_set, **kwargs)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """集成预测 - 加权平均。"""
        predictions = []
        for model in self.models:
            pred = model.predict(X)
            predictions.append(pred)

        # 加权平均
        weighted_pred = np.zeros_like(predictions[0])
        for pred, weight in zip(predictions, self.weights):
            weighted_pred += pred * weight

        return weighted_pred

    def save(self, path: Path) -> None:
        """保存所有子模型。"""
        path.mkdir(parents=True, exist_ok=True)
        for i, model in enumerate(self.models):
            model_path = path / f"model_{i}.json"
            model.save(model_path)

        # 保存权重
        import json
        meta = {
            "weights": self.weights,
            "model_types": [m.model_type for m in self.models],
        }
        with open(path / "ensemble_meta.json", "w") as f:
            json.dump(meta, f)

    @classmethod
    def load(cls, path: Path) -> EnsembleModel:
        """加载集成模型。"""
        import json

        with open(path / "ensemble_meta.json") as f:
            meta = json.load(f)

        models = []
        for i, model_type in enumerate(meta["model_types"]):
            model_path = path / f"model_{i}.json"
            if model_type == "XGBoostModel":
                models.append(XGBoostModel.load(model_path))
            elif model_type == "LightGBMModel":
                models.append(LightGBMModel.load(model_path))
            else:
                raise ValueError(f"Unknown model type: {model_type}")

        return cls(models=models, weights=meta["weights"])

    @property
    def xgb_weight(self) -> float:
        """XGBoost 权重。"""
        for i, model in enumerate(self.models):
            if isinstance(model, XGBoostModel):
                return self.weights[i]
        return 0.0

    @property
    def lgbm_weight(self) -> float:
        """LightGBM 权重。"""
        for i, model in enumerate(self.models):
            if isinstance(model, LightGBMModel):
                return self.weights[i]
        return 0.0
