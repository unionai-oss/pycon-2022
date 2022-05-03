# %%
from typing import Union

import numpy as np
import torch
import torch.nn as nn

from transformers import EvalPrediction

from flytekit import Resources
from unionml import Dataset, Model

from pictionary_app.dataset import QuickDrawDataset, get_quickdraw_class_names
from pictionary_app.trainer import (
    init_model,
    quickdraw_compute_metrics,
    quickdraw_trainer,
)


# %% [markdown]
# ## Mechanics of a UnionML app
#
# A UnionML App consists of 2 major components:
#
# 1. `Dataset`: This encapsulates the data that will be used to train a model. It can
#    use image data or structured data. In this example, we'll use the doodle dataset
#    here.
# 2. `Model`: This encapsulates functionality for training a model, evaluating it on
#    some data, and generating predictions from features.

dataset = Dataset(name="quickdraw_dataset", test_size=0.2, shuffle=True)
model = Model(name="quickdraw_classifier", init=init_model, dataset=dataset)

# %% [markdown]
# Define compute resource requirements:

reader_resources = Resources(cpu="1", mem="6Gi")
trainer_resources = Resources(gpu="1", mem="6Gi")

# %% [markdown]
# ## Reading Data
#
# This method reads data from the "outside world" and outputs it in a form that's ready
# for model training.

@dataset.reader(
    cache=True,
    cache_version="1.2",
    requests=reader_resources,
    limits=reader_resources,
)
def reader(
    data_dir: str, max_examples_per_class: int = 1000, class_limit: int = 5
) -> QuickDrawDataset:
    return QuickDrawDataset(data_dir, max_examples_per_class, class_limit=class_limit)


# %% [markdown]
# Next, we defines how to parse out features from the dataset we produced above.
# This will be used in the predictor function below.

# %%
@dataset.feature_loader
def feature_loader(data: Union[QuickDrawDataset, np.ndarray]) -> torch.Tensor:
    if isinstance(data, np.ndarray):
        return torch.tensor(data, dtype=torch.float32).unsqueeze(0).unsqueeze(0) / 255.0
    return torch.stack([data[i][0] for i in range(len(data))])


# %% [markdown]
# ## Train the Model 🦾
#
# Specify how to train your model on Flyte. Here we use the utility function
# `quickdraw_trainer`, which uses the `transformers` library to implement the
# training routine.

# %%
@model.trainer(
    cache=True,
    cache_version="1.2",
    requests=trainer_resources,
    limits=trainer_resources,
)
def trainer(
    module: nn.Module, dataset: torch.utils.data.Subset, *, num_epochs: int = 20
) -> nn.Module:
    module = quickdraw_trainer(module, dataset, num_epochs)
    return module.cpu()  # convert model to cpu before serializing


# %% [markdown]
# ## Implement the Evaluation Criteria ❌
#
# To train a model correctly, provide the evaluation criteria.

# %%
@model.evaluator
def evaluator(module: nn.Module, dataset: QuickDrawDataset) -> float:
    if torch.cuda.is_available():
        cuda = True
        module = module.cuda()

    acc = []
    for features, label_ids in torch.utils.data.DataLoader(dataset, batch_size=256):
        if cuda:
            features, label_ids = features.to("cuda"), label_ids.to("cuda")
        metrics = quickdraw_compute_metrics(
            EvalPrediction(module(features), label_ids)
        )
        acc.append(metrics["acc1"])
    return float(sum(acc) / len(acc))


# %% [markdown]
# ### Generate Predictions 🔮
#
# Once the model is trained, we can implement the `predictor` function, which specifies
# how to generate predictions from a tensor of features.

# %%
@model.predictor(cache=True, cache_version="1.2")
def predictor(module: nn.Module, features: torch.Tensor) -> dict:
    module.eval()
    if torch.cuda.is_available():
        module, features = module.cuda(), features.cuda()
    with torch.no_grad():
        probabilities = nn.functional.softmax(module(features)[0], dim=0)
    class_names = get_quickdraw_class_names()
    values, indices = torch.topk(probabilities, 3)
    return {class_names[i]: v.item() for i, v in zip(indices, values)}


# %% [markdown]
# ## Scaling 🏔
#
# One reason why you want to use UnionML is if you want to effortlessly scale
# your training to a large cluster. You can do this by attaching a backend
# cluster to your UnionML app by invoking the `model.remote` method.
#
# Under the hood, UnionML uses Flyte as a way of abstracting away cluster
# resource management, scheduling, and orchestration so that you can focus
# on optimizing your models 🤖 and curating high-quality data 📊!
#
# Below we attach a backend cluster pointing the Union.ai playground.

model.remote(
    registry="ghcr.io/unionai-oss",
    dockerfile="Dockerfile.gpu",
    config_file_path="config/config-remote.yaml",
    project="unionml",
    domain="development",
)

# %% [markdown]
# ## It's Just Python!
#
# All of the functions we implemented above are just python and can be invoked
# as python functions! Similarly, a `unionml.Model` can be used to train a model
# locally.

# %%
if __name__ == "__main__":
    num_classes = 2
    model.train(
        hyperparameters={"num_classes": num_classes},
        trainer_kwargs={"num_epochs": 1},
        data_dir="./data",
        max_examples_per_class=10000,
        class_limit=num_classes,
    )
