import ivy
import inspect
import importlib
import numpy as np
from types import ModuleType


wrapped_modules_n_classes = []
NON_WRAPPED_FUNCTIONS = [
    "copy_nest",
    "current_backend",
    "current_backend_str",
    "set_backend",
    "get_backend",
    "unset_backend",
    "get_referrers_recursive",
    "set_debug_mode",
    "set_breakpoint_debug_mode",
    "set_exception_debug_mode",
    "unset_debug_mode",
    "debug_mode",
    "nested_map",
    "to_ivy",
    "args_to_ivy",
    "to_native",
    "args_to_native",
    "default",
    "exists",
    "set_min_base",
    "get_min_base",
    "set_min_denominator",
    "get_min_denominator",
    "split_func_call_across_gpus",
    "cache_fn",
    "split_func_call",
    "compile",
    "compile_graph",
    "dev",
    "as_ivy_dev",
    "as_native_dev",
    "memory_on_dev",
    "gpu_is_available",
    "num_gpus",
    "tpu_is_available",
    "dtype",
    "as_ivy_dtype",
    "cprint",
    "to_ivy_module",
    "tree_flatten",
    "tree_unflatten",
    "start_compiling",
    "stop_compiling",
    "get_compiled",
    "index_nest",
    "set_nest_at_index",
    "map_nest_at_index",
    "multi_index_nest",
    "set_nest_at_indices",
    "map_nest_at_indices",
    "nested_indices_where",
    "map",
    "set_default_device",
    "unset_default_device",
    "closest_valid_dtype",
    "default_dtype",
    "default_device",
    "as_native_dtype",
    "is_ivy_array",
    "is_ivy_container",
    "inplace_update",
    "inplace_increment",
    "inplace_decrement",
    "prune_nest_at_index",
    "prune_nest_at_indices",
    "is_array",
    "is_native_array",
    "nested_any",
    "fn_array_spec",
    "insert_into_nest_at_index",
    "insert_into_nest_at_indices",
    "vec_sig_fig",
    "native_array",
]
FUNCTIONS_W_CONT_SUPPORT = [
    "multi_head_attention",
    "execute_with_gradients",
    "adam_step",
    "optimizer_update",
    "gradient_descent_update",
    "lars_update",
    "adam_update",
    "lamb_update",
    "stable_divide",
    "stable_pow",
]
ARRAYLESS_RET_FUNCTIONS = [
    "to_numpy",
    "to_list",
    "to_scalar",
    "is_native_array",
    "is_ivy_array",
    "is_variable",
]
NESTED_ARRAY_RET_FUNCTIONS = ["unstack", "split"]
NON_DTYPE_WRAPPED_FUNCTIONS = ["arange", "asarray", "array", "full", "prod", "sum"]
NON_DEV_WRAPPED_FUNCTIONS = []

FW_FN_KEYWORDS = {
    "numpy": [],
    "jax": [],
    "tensorflow": [],
    "torch": [],
    "mxnet": ["ndarray"],
}

NATIVE_KEYS_TO_SKIP = {
    "numpy": [],
    "jax": [],
    "tensorflow": [],
    "torch": [
        "classes",
        "torch",
        "is_grad_enabled",
        "get_default_dtype",
        "numel",
        "clone",
        "cpu",
        "set_",
        "type",
        "requires_grad_",
    ],
    "mxnet": [],
}


# Functions #


def _wrap_function(fn):
    """
    Creates a wrapped ivy version of the function if it is not a private function and
    not in the non wrapped functions list. This allows the new function to accept as
    inputs an ivy array before performing the required o  peration and then returning
    an ivy array.

    Parameters
    ----------
    fn
        native function to be wrapped

    Returns
    -------
        The wrapped version of the function with all the necessary attributes updated.
    """
    # determine whether the function has an out argument
    keys = inspect.signature(fn).parameters.keys()
    handle_out_with_backend = "out" in keys
    handle_dtype = "dtype" in keys
    handle_dev = "device" in keys

    # do nothing if the function is private or in the non wrapped functions list
    if hasattr(fn, "__name__") and (
        fn.__name__[0] == "_" or fn.__name__ in NON_WRAPPED_FUNCTIONS
    ):
        return fn

    # do nothing if the function is already wrapped
    if hasattr(fn, "wrapped") and fn.wrapped:
        return fn

    def _function_w_arrays_n_out_handled(*args, out=None, **kwargs):
        """
        Converts all :code:`ivy.Array` instances in both the positional and
        keyword arguments into :code:`ivy.NativeArray` instances, calls the internal
        function :code:`fn`, and then converts all :code:`ivy.NativeArray` instances
        in the return back to :code:`ivy.Array` instances. Also handles :code:`out`
        argument correctly, enabling an inplace update.

        Parameters
        ----------
        args
            The arguments to be passed to the function.

        out
            optional output array, for writing the result to.

        kwargs
            The key word arguments to be passed  to the function.

        Returns
        -------
            The result of computing the function fn as an ivy array or a native array.
        """
        # convert all arrays in the inputs to ivy.NativeArray instances
        native_args, native_kwargs = ivy.args_to_native(
            *args, **kwargs, include_derived={tuple: True}
        )
        if ivy.exists(out):
            # extract underlying native array for out
            native_out = ivy.to_native(out)
            if handle_out_with_backend:
                # compute return, with backend inplace update handled by
                # the backend function
                ret = fn(*native_args, out=native_out, **native_kwargs)
            else:
                # compute return, with backend inplace update handled explicitly
                ret = fn(*native_args, **native_kwargs)
                ret = ivy.inplace_update(native_out, ivy.to_native(ret))
        else:
            ret = fn(*native_args, **native_kwargs)
        if fn.__name__ in ARRAYLESS_RET_FUNCTIONS + NESTED_ARRAY_RET_FUNCTIONS:
            return ret
        elif ivy.exists(out):
            # handle ivy.Array inplace update as well
            out.data = ivy.to_native(ret)
            return out
        # convert all returned arrays to ivy.Array instances
        return ivy.to_ivy(ret, nested=True, include_derived={tuple: True})

    def _get_first_array(*args, **kwargs):
        # ToDo: make this more efficient, with function ivy.nested_nth_index_where
        arr = None
        if args:
            arr_idxs = ivy.nested_indices_where(args, ivy.is_array)
            if arr_idxs:
                arr = ivy.index_nest(args, arr_idxs[0])
            else:
                arr_idxs = ivy.nested_indices_where(kwargs, ivy.is_array)
                if arr_idxs:
                    arr = ivy.index_nest(kwargs, arr_idxs[0])
        elif kwargs:
            arr_idxs = ivy.nested_indices_where(kwargs, ivy.is_array)
            if arr_idxs:
                arr = ivy.index_nest(kwargs, arr_idxs[0])
        return arr

    def _function_w_arrays_dtype_n_dev_handled(
        *args, dtype=None, device=None, **kwargs
    ):
        if handle_dtype or handle_dev:
            arr = _get_first_array(*args, **kwargs)
            if handle_dtype:
                if fn.__name__ not in NON_DTYPE_WRAPPED_FUNCTIONS:
                    dtype = ivy.default_dtype(dtype, item=arr, as_native=True)
                kwargs["dtype"] = dtype
            if handle_dev:
                if fn.__name__ not in NON_DEV_WRAPPED_FUNCTIONS:
                    device = ivy.default_device(device, item=arr, as_native=True)
                kwargs["device"] = device
        return _function_w_arrays_n_out_handled(*args, **kwargs)

    def _function_wrapped(*args, **kwargs):
        """
        Computes the result of the function fn, returning the result as an ivy array,
        a native framework array, or an ivy container.

        Parameters
        ----------
        args
            The arguments to be passed to the function.

        kwargs
            The key word arguments to be passed to the function.

        Returns
        -------
            The result of computing the function fn as an ivy array, a native array,
            or an ivy container.
        """
        fn_name = fn.__name__
        """ 
        if the function is not implemented for containers or the function 
        has built-in container support, call the function using the passed 
        arguments directly, returning an ivy or a native array.
        """
        if not hasattr(ivy.Container, fn_name) or fn_name in FUNCTIONS_W_CONT_SUPPORT:
            return _function_w_arrays_dtype_n_dev_handled(*args, **kwargs)
        """
        if any of the arguments or keyword arguments passed to the function contains a 
        a container, get the container's version of the function and call it using
        the passed arguments.
        """
        if ivy.nested_any(
            args, ivy.is_ivy_container, check_nests=True
        ) or ivy.nested_any(kwargs, ivy.is_ivy_container, check_nests=True):
            f = getattr(ivy.Container, "static_" + fn_name)
            return f(*args, **kwargs)

        """
        if the passed arguments does not contain a container, the function using 
        the passed arguments, returning an ivy or a native array.
        """
        return _function_w_arrays_dtype_n_dev_handled(*args, **kwargs)

    if hasattr(fn, "__name__"):
        _function_wrapped.__name__ = fn.__name__
    _function_wrapped.wrapped = True
    _function_wrapped.inner_fn = fn
    if hasattr(fn, "array_spec"):
        _function_wrapped.array_spec = fn.array_spec
    if hasattr(fn, "reduce"):
        _function_wrapped.reduce = fn.reduce

    return _function_wrapped


def _unwrap_function(function_wrapped):
    """
    Unwraps the function in function_wrapped.

    Parameters
    ----------
    function_wrapped
        The function to be unwrapped.

    Returns
    -------
    The unwrapped version of the function which is the same as the passed function
    for unwrapped functions and the inner_fn if the function is wrapped.
    The newly unwrapped function accepts inputs and returns outputs as native arrays
    instead of ivy arrays.
    """
    if not hasattr(function_wrapped, "wrapped") or not function_wrapped.wrapped:
        return function_wrapped
    return function_wrapped.inner_fn


def _invalid_fn(fn, fs=None):
    if fs is None:
        fs = ivy.current_backend_str()
    if isinstance(fn, np.ufunc):
        return False
    if not hasattr(fn, "__module__") or not fn.__module__:
        return True
    fw_fn_keywords = ["ivy", fs] + FW_FN_KEYWORDS[fs]
    for kw in fw_fn_keywords:
        if kw in fn.__module__:
            return False
    return True


def _wrap_or_unwrap_functions(
    wrap_or_unwrap_fn, val=None, fs=None, classes_to_wrap=None, native=False, depth=0
):
    classes_to_wrap = [] if classes_to_wrap is None else classes_to_wrap
    if fs is None:
        fs = ivy.current_backend_str()
    if val is None:
        val = importlib.import_module(ivy.current_backend_str()) if native else ivy
    str_to_check = fs if native else "ivy"
    is_class = inspect.isclass(val)
    if isinstance(val, ModuleType) or (val in classes_to_wrap):
        if val in wrapped_modules_n_classes or (
            (
                "__file__" not in val.__dict__
                or (str_to_check not in val.__file__)
                or "framework_handler" in val.__file__
            )
            and not is_class
        ):
            return val
        wrapped_modules_n_classes.append(val)
        if is_class:
            for k in dir(val):
                if native and (k in NATIVE_KEYS_TO_SKIP[fs]):
                    continue
                v = getattr(val, k)
                if v is not None:
                    # noinspection PyBroadException
                    try:
                        setattr(
                            val,
                            k,
                            _wrap_or_unwrap_functions(
                                wrap_or_unwrap_fn,
                                v,
                                fs,
                                classes_to_wrap,
                                native,
                                depth + 1,
                            ),
                        )
                    except Exception:
                        pass
        else:
            for k, v in val.__dict__.items():
                if native and (k in NATIVE_KEYS_TO_SKIP[fs] or k[0] == "_"):
                    continue
                if v is None:
                    val.__dict__[k] = v
                else:
                    # noinspection PyBroadException
                    try:
                        val.__dict__[k] = _wrap_or_unwrap_functions(
                            wrap_or_unwrap_fn, v, fs, classes_to_wrap, native, depth + 1
                        )
                    except Exception:
                        pass
        if depth == 0:
            wrapped_modules_n_classes.clear()
        return val
    elif callable(val) and not is_class:
        if depth == 0:
            wrapped_modules_n_classes.clear()
        if (
            hasattr(val, "inner_fn") and (_invalid_fn(val.inner_fn) and not native)
        ) or (_invalid_fn(val) and not native):
            return val
        return wrap_or_unwrap_fn(val)
    if depth == 0:
        wrapped_modules_n_classes.clear()
    return val


def _wrap_functions():
    return _wrap_or_unwrap_functions(_wrap_function)


def _unwrap_functions():
    return _wrap_or_unwrap_functions(_unwrap_function)
