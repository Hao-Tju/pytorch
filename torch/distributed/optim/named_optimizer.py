import logging
import warnings

from copy import deepcopy
from typing import Any, Collection, Dict, Mapping, Union

import torch
from torch import optim
from torch.distributed._shard.sharded_tensor import ShardedTensor


__all__ = ["NamedOptimizer"]

logger = logging.getLogger(__name__)


class NamedOptimizer(optim.Optimizer):
    """
    NamedOptimizer takes a dict of parameters and exposes state_dict by parameter key.
    We replace the original key (number) in an optim to the FQN string. User can
    initialize the optim as they initialize a PyTorch optim, the only difference is
    that they also need to passed in the FQN of each parameters.

    Args:
        named_parameters (Mapping[str, Union[torch.Tensor, ShardedTensor]]):
            Parameters of the module and its FQN.
        optimizer_class (optim.Optimizer):
            the class of optimizer to instantiate.
        param_groups (Collection[Mapping[str, Any]]):
            param_groups to pass to optimizer if specified.
            Default: None
        args: arguments to pass to the optimizer constructor.
        kwargs: arguments to pass to the optimizer constructor.

    Example::
        >>> # xdoctest: +SKIP("distributed")
        >>> from torch import optim
        >>> from torch.distributed.optim import NamedOptimizer
        >>>
        >>> # Define the named optimizer.
        >>> m = Model(...)
        >>> named_optim = NamedOptimizer(m.named_parameters(), optim.SGD)
        >>> # Forward pass + backward pass.
        >>> named_optim.step()
        >>> ...
        >>> # Call state_dict for the named optimizer returns a FQN state_dict.
        >>> named_optim.state_dict()

    __ TODO: Add tutorial for NamedOptimizer.
    """

    def __init__(
        self,
        named_parameters: Mapping[str, Union[torch.Tensor, ShardedTensor]],
        optimizer_class: optim.Optimizer,
        param_groups: Collection[Mapping[str, Any]] = None,
        *args,
        **kwargs,
    ) -> None:
        torch._C._log_api_usage_once("torch.distributed.optim.NamedOptimizer")
        self.param_groups: Collection[Mapping[str, Any]] = param_groups  # type: ignore[assignment]
        self.named_parameters = dict(named_parameters)
        params_optimizer = (
            self.named_parameters.values() if param_groups is None else param_groups
        )
        self._optimizer = optimizer_class(  # type: ignore[operator]
            params_optimizer,
            *args,
            **kwargs,
        )
        if param_groups is None:
            self.param_keys_order = list(self.named_parameters.keys())
        else:
            warnings.warn(
                "Since we pass in param_groups, we will use param_groups to "
                "initialize the optimizer, not all parameters of the module."
            )
            param_to_key = {param: key for key, param in self.named_parameters}  # type: ignore[misc, has-type]
            param_keys_order = []
            for group in param_groups:
                for param in group["params"]:
                    if param not in param_to_key:
                        raise ValueError("Param name not found for param group.")
                    param_keys_order.append(param_to_key[param])
            self.param_keys_order = param_keys_order

    def state_dict(self) -> Dict[str, Any]:
        """
        Return the state_dict of the optimzer. Instead of using number to index
        parameters, we will use module fully qualifed name (FQN) as the key.
        """
        state_dict = self._optimizer.state_dict()
        param_groups = state_dict["param_groups"]

        ret_state = {
            self.param_keys_order[st_key]: state_val
            for st_key, state_val in state_dict["state"].items()
        }

        ret_groups = []
        for group in param_groups:
            param_keys = []
            for param in group["params"]:
                param_keys.append(self.param_keys_order[param])
            ret_group = {"params": sorted(param_keys)}
            for k, v in group.items():
                if k != "params":
                    ret_group[k] = deepcopy(v)
            ret_groups.append(ret_group)

        ret: Dict[str, object] = {"state": ret_state}
        ret["param_groups"] = ret_groups
        return ret

    def register_state_dict_pre(self):
        raise NotImplementedError()

    def register_state_dict_post(self):
        raise NotImplementedError()

    def step(self):
        """
        Performs a single optimization step.

        This will call :meth:`torch.optim.Optimizer.step` on the wrapped
        optimizer.
        """
        self._optimizer.step()

    def load_state_dict(self, state_dict: Mapping[str, Any]) -> None:
        """
        This public function defines the default behavior to load a state_dict
        for named optimizer.

        Sample Code
        ```
            my_model = MyModule()
            optimizer = NamedOptimizer(my_model.named_parameters(), Adagrad)
            ...

            optim_state_dict = optimizer.state_dict()
            ...

            fs_storage_loader = torch.distributed.FileSystemLoader("/checkpoint/1")
            torch.distributed.load_state_dict(
                state_dict=optim_state_dict,
                storage_reader=fs_storage_loader,
            )

            optim_state_dict.load_state_dict(optim_state_dict)
            ...
        ```
        Args:
            state_dict (Dict[str, Any]) : A state_dict to load to. Note that this
                state dict will updated in places.
        """
        new_state_dict = self._optimizer.state_dict()
        state = state_dict["state"]
        new_state = new_state_dict["state"]

        # Load state of state_dict
        if len(new_state) != len(state):
            raise ValueError(
                f"Different parameter count: {len(new_state)} vs {len(state)}"
            )
        for idx, param_key in enumerate(self.param_keys_order):
            if param_key not in state.keys():
                raise ValueError(f"Parameter {param_key} not found")
            if len(state[param_key]) != len(new_state[idx]):
                raise ValueError(
                    f"Different state size: {len(state[param_key])} vs {len(new_state[idx])}"
                )
            # Iterate through all optimizer states.
            for state_key, state_val in new_state[idx].items():
                if state_key not in state[param_key]:
                    raise ValueError(
                        f"State key {state_key} not found for param {param_key}"
                    )

                src_state_val = state[param_key][state_key]
                if isinstance(state_val, ShardedTensor):
                    assert isinstance(src_state_val, ShardedTensor)
                    num_shards = len(state_val.local_shards())
                    num_new_shards = len(src_state_val.local_shards())
                    if num_shards != num_new_shards:
                        raise ValueError(
                            f"Different number of shards {num_shards} vs {num_new_shards} for {param_key}/{state_key}"
                        )
                    for shard, src_shard in zip(
                        state_val.local_shards(), src_state_val.local_shards()
                    ):
                        shard.tensor.detach().copy_(src_shard.tensor)
                elif isinstance(state_val, torch.Tensor):
                    assert isinstance(src_state_val, torch.Tensor)
                    state_val.detach().copy_(src_state_val)
                else:
                    new_state[idx][state_key] = deepcopy(src_state_val)

        # Load param_groups of state_dict
        src_param_groups = state_dict["param_groups"]
        new_param_groups = new_state_dict["param_groups"]

        if len(new_param_groups) != len(src_param_groups):
            raise ValueError(
                f"Different param_groups count: {len(new_param_groups)} vs {len(src_param_groups)}"
            )
        src_group_map = {}
        for group in src_param_groups:
            param_keys = []
            for param_key in group["params"]:
                param_keys.append(param_key)
            src_group_map["/".join(sorted(param_keys))] = group
        new_group_map = {}
        for new_group in new_param_groups:
            param_keys = []
            for param_key in new_group["params"]:
                param_keys.append(self.param_keys_order[param_key])  # type: ignore[call-overload]
            new_group_map["/".join(sorted(param_keys))] = new_group
        for group_key, new_group in new_group_map.items():
            if group_key not in src_group_map:
                raise ValueError(f"Group {group_key} not found")
            src_group = src_group_map[group_key]
            if len(src_group) != len(new_group):
                raise ValueError(
                    f"Different param_group size: {len(src_group)} vs {len(new_group)}"
                )
            for k in src_group:
                if k not in new_group:
                    raise ValueError(f"Group key {k} not found for group {group_key}")
                if k != "params":
                    new_group[k] = deepcopy(src_group[k])

        self._optimizer.load_state_dict(new_state_dict)

    # pyre-ignore [2]
    def add_param_group(self, param_group: Any) -> None:
        raise NotImplementedError()
