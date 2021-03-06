# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""A class to store named variables and a scope operator to manage sharing."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import contextlib
import traceback

import six

from tensorflow.python.framework import dtypes
from tensorflow.python.framework import ops
from tensorflow.python.framework import tensor_shape
from tensorflow.python.ops import init_ops
from tensorflow.python.ops import variables
from tensorflow.python.platform import logging

__all__ = ["VariableScope", "get_variable_scope", "get_variable",
           "variable_scope", "variable_op_scope", "no_regularizer"]


class _VariableStore(object):
  """Variable store that carries a number of named Variables.

  New variable names and new variables can be created; all stored
  variables are initialized with the initializer passed to __init__.

  Attributes:
    vars: a dictionary with string names (same as passed in GetVar) as keys
          and the corresponding TensorFlow Variables as values.
  """

  def __init__(self):
    """Create a variable store."""
    self._vars = {}  # A dictionary of the stored TensorFlow variables.

  def get_variable(self, name, shape=None, dtype=dtypes.float32,
                   initializer=None, regularizer=None, reuse=None,
                   trainable=True, collections=None, caching_device=None):
    """Gets an existing variable with these parameters or create a new one.

    If a variable with the given name is already stored, we return the stored
    variable. Otherwise, we create a new one.

    Set `reuse` to `True` when you only want to reuse existing Variables.
    Set `reuse` to `False` when you only want to create new Variables.
    If `reuse` is `None` (the default), both new and existing variables are
    returned.

    If initializer is `None` (the default), the default initializer passed in
    the constructor is used. If that one is `None` too, we use a new
    `UniformUnitScalingInitializer`. If initializer is a Tensor, we use
    it as a value and derive the shape from the initializer.

    Args:
      name: the name of the new or existing variable.
      shape: shape of the new or existing variable.
      dtype: type of the new or existing variable (defaults to `DT_FLOAT`).
      initializer: initializer for the variable.
      regularizer: a (Tensor -> Tensor or None) function; the result of
        applying it on a newly created variable will be added to the collection
        GraphKeys.REGULARIZATION_LOSSES and can be used for regularization.
      reuse: a Boolean or `None`. Controls reuse or creation of variables.
      trainable: If `True` also add the variable to the graph collection
        `GraphKeys.TRAINABLE_VARIABLES` (see tf.Variable).
      collections: List of graph collections keys to add the Variable to.
        Defaults to `[GraphKeys.VARIABLES]` (see tf.Variable).
      caching_device: Optional device string or function describing where the
        Variable should be cached for reading.  Defaults to the Variable's
        device.  If not `None`, caches on another device.  Typical use is to
        cache on the device where the Ops using the Variable reside, to
        deduplicate copying through `Switch` and other conditional statements.

    Returns:
      The created or existing variable.

    Raises:
      ValueError: when creating a new variable and shape is not declared,
        when reusing a variable and specifying a conflicting shape,
        or when violating reuse during variable creation.
    """
    # Set to true if initializer is a constant.
    initializing_from_value = False
    if initializer is not None and isinstance(initializer, ops.Tensor):
      initializing_from_value = True
    if shape is not None and initializing_from_value:
      raise ValueError("If initializer is a constant, do not specify shape.")

    should_check = reuse is not None
    dtype = dtypes.as_dtype(dtype)
    shape = tensor_shape.as_shape(shape)

    if name in self._vars:
      # Here we handle the case when returning an existing variable.
      if should_check and not reuse:
        tb = self._vars[name].op.traceback[::-1]
        # Throw away internal tf entries and only take a few lines.
        tb = [x for x in tb if "tensorflow/python" not in x[0]][:3]
        raise ValueError("Variable %s already exists, disallowed."
                         " Did you mean to set reuse=True in VarScope? "
                         "Originally defined at:\n\n%s" % (
                             name, "".join(traceback.format_list(tb))))
      found_var = self._vars[name]
      if not shape.is_compatible_with(found_var.get_shape()):
        raise ValueError("Trying to share variable %s, but specified shape %s"
                         " and found shape %s." % (name, shape,
                                                   found_var.get_shape()))
      if not dtype.is_compatible_with(found_var.dtype):
        dtype_str = dtype.name
        found_type_str = found_var.dtype.name
        raise ValueError("Trying to share variable %s, but specified dtype %s"
                         " and found dtype %s." % (name, dtype_str,
                                                   found_type_str))
      return found_var

    # The code below handles only the case of creating a new variable.
    if should_check and reuse:
      raise ValueError("Variable %s does not exist, disallowed."
                       " Did you mean to set reuse=None in VarScope?" % name)
    if not shape.is_fully_defined() and not initializing_from_value:
      raise ValueError("Shape of a new variable (%s) must be fully defined, "
                       "but instead was %s." % (name, shape))

    # Create the tensor to initialize the variable.
    if initializer is None:
      initializer = init_ops.uniform_unit_scaling_initializer()
    # Clear control dependencies while creating the initializer.
    with ops.control_dependencies(None):
      if initializing_from_value:
        init_val = initializer
      else:
        with ops.name_scope(name + "/Initializer/"):
          init_val = initializer(shape.as_list(), dtype=dtype)

    # Create the variable.
    v = variables.Variable(init_val, name=name, trainable=trainable,
                           collections=collections,
                           caching_device=caching_device)
    self._vars[name] = v
    logging.info("Created variable %s with shape %s and init %s", v.name,
                 format(shape), initializer)

    # Run the regularizer if requested and save the resulting loss.
    if regularizer:
      with ops.name_scope(name + "/Regularizer/"):
        loss = regularizer(v)
      if loss is not None:
        logging.info("Applied regularizer to %s and added the result %s to "
                     "REGULARIZATION_LOSSES.", v.name, loss.name)
        ops.add_to_collection(ops.GraphKeys.REGULARIZATION_LOSSES, loss)

    return v


# To stop regularization, use this regularizer
def no_regularizer(_):
  """Use this function to prevent regularization of variables."""
  return None


class VariableScope(object):
  """Variable scope object to carry defaults to provide to get_variable.

  Many of the arguments we need for get_variable in a variable store are most
  easily handled with a context. This object is used for the defaults.

  Attributes:
    name: name of the current scope, used as prefix in get_variable.
    initializer: default initializer passed to get_variable.
    regularizer: default regularizer passed to get_variable.
    reuse: Boolean or None, setting the reuse in get_variable.
    caching_device: string, callable, or None: the caching device passed to
      get_variable.
    name_scope: The name passed to tf.name_scope.
  """

  def __init__(self, reuse, name="", initializer=None, regularizer=None,
               caching_device=None, name_scope=""):
    """Creates a new VariableScope with the given properties."""
    self._name = name
    self._initializer = initializer
    self._regularizer = regularizer
    self._reuse = reuse
    self._caching_device = caching_device
    self._name_scope = name_scope

  @property
  def name(self):
    return self._name

  @property
  def reuse(self):
    return self._reuse

  @property
  def initializer(self):
    return self._initializer

  @property
  def regularizer(self):
    return self._regularizer

  @property
  def caching_device(self):
    return self._caching_device

  def reuse_variables(self):
    """Reuse variables in this scope."""
    self._reuse = True

  def set_initializer(self, initializer):
    """Set initializer for this scope."""
    self._initializer = initializer

  def set_regularizer(self, regularizer):
    """Set regularizer for this scope."""
    self._regularizer = regularizer

  def set_caching_device(self, caching_device):
    """Set caching_device for this scope."""
    self._caching_device = caching_device

  def get_variable(self, var_store, name, shape=None, dtype=dtypes.float32,
                   initializer=None, regularizer=None,
                   trainable=True, collections=None, caching_device=None):
    """Gets an existing variable with this name or create a new one."""
    if initializer is None:
      initializer = self._initializer
    if regularizer is None:
      regularizer = self._regularizer
    if caching_device is None:
      caching_device = self._caching_device

    full_name = self.name + "/" + name if self.name else name
    # Variable names only depend on variable_scope (full_name here),
    # not name_scope, so we reset it below for the time of variable creation.
    with ops.name_scope(None):
      return var_store.get_variable(
          full_name, shape=shape, dtype=dtype, initializer=initializer,
          regularizer=regularizer, reuse=self.reuse, trainable=trainable,
          collections=collections, caching_device=caching_device)


_VARSTORE_KEY = ("__variable_store",)
_VARSCOPE_KEY = ("__varscope",)


def get_variable_scope():
  """Returns the current variable scope."""
  scope = ops.get_collection(_VARSCOPE_KEY)
  if scope:  # This collection has at most 1 element, the default scope at [0].
    return scope[0]
  scope = VariableScope(False)
  ops.add_to_collection(_VARSCOPE_KEY, scope)
  return scope


def _get_default_variable_store():
  store = ops.get_collection(_VARSTORE_KEY)
  if store:
    return store[0]
  store = _VariableStore()
  ops.add_to_collection(_VARSTORE_KEY, store)
  return store


def get_variable(name, shape=None, dtype=dtypes.float32, initializer=None,
                 regularizer=None, trainable=True,
                 collections=None):
  """Gets an existing variable with these parameters or create a new one.

  This function prefixes the name with the current variable scope
  and performs reuse checks. See the
  [Variable Scope How To](../../how_tos/variable_scope/index.md)
  for an extensive description of how reusing works. Here is a basic example:

  ```python
  with tf.variable_scope("foo"):
      v = tf.get_variable("v", [1])  # v.name == "foo/v:0"
      w = tf.get_variable("w", [1])  # w.name == "foo/w:0"
  with tf.variable_scope("foo", reuse=True)
      v1 = tf.get_variable("v")  # The same as v above.
  ```

  If initializer is `None` (the default), the default initializer passed in
  the variable scope will be used. If that one is `None` too, a
  `UniformUnitScalingInitializer` will be used. The initializer can also be
  a Tensor, in which case the variable is initialized to this value and shape.

  Similarly, if the regularizer is `None` (the default), the default regularizer
  passed in the variable scope will be used (if that is `None` too,
  then by default no regularization is performed).

  Args:
    name: the name of the new or existing variable.
    shape: shape of the new or existing variable.
    dtype: type of the new or existing variable (defaults to `DT_FLOAT`).
    initializer: initializer for the variable if one is created.
    regularizer: a (Tensor -> Tensor or None) function; the result of
      applying it on a newly created variable will be added to the collection
      GraphKeys.REGULARIZATION_LOSSES and can be used for regularization.
    trainable: If `True` also add the variable to the graph collection
      `GraphKeys.TRAINABLE_VARIABLES` (see tf.Variable).
    collections: List of graph collections keys to add the Variable to.
      Defaults to `[GraphKeys.VARIABLES]` (see tf.Variable).

  Returns:
    The created or existing variable.

  Raises:
    ValueError: when creating a new variable and shape is not declared,
      or when violating reuse during variable creation. Reuse is set inside
      `variable_scope`.
  """
  return get_variable_scope().get_variable(
      _get_default_variable_store(), name, shape=shape, dtype=dtype,
      initializer=initializer, regularizer=regularizer, trainable=trainable,
      collections=collections)


@contextlib.contextmanager
def _pure_variable_scope(name_or_scope, reuse=None, initializer=None,
                         regularizer=None, caching_device=None):
  """Creates a context for the variable_scope, see `variable_scope` for docs.

  Note: this does not create a name scope.

  Args:
    name_or_scope: `string` or `VariableScope`: the scope to open.
    reuse: `True` or `None`; if `True`, we go into reuse mode for this scope as
      well as all sub-scopes; if `None`, we just inherit the parent scope reuse.
    initializer: default initializer for variables within this scope.
    regularizer: default regularizer for variables within this scope.
    caching_device: default caching device for variables within this scope.

  Yields:
    A scope that can be to captured and reused.

  Raises:
    ValueError: when trying to reuse within a create scope, or create within
      a reuse scope, or if reuse is not `None` or `True`.
    TypeError: when the types of some arguments are not appropriate.

  """
  get_variable_scope()  # Ensure that a default exists, then get a pointer.
  default_varscope = ops.get_collection(_VARSCOPE_KEY)
  try:
    old = default_varscope[0]
    reuse = reuse or old.reuse  # Re-using is inherited by sub-scopes.
    if isinstance(name_or_scope, VariableScope):
      name_scope = name_or_scope._name_scope  # pylint: disable=protected-access
      # Handler for the case when we jump to a shared scope.
      #   We create a new VariableScope (default_varscope[0]) that contains
      #   a copy of the provided shared scope, possibly with changed reuse
      #   and initializer, if the user requested this.
      default_varscope[0] = VariableScope(
          reuse, name=name_or_scope.name,
          initializer=name_or_scope.initializer,
          regularizer=name_or_scope.regularizer,
          caching_device=name_or_scope.caching_device,
          name_scope=name_scope)
      if initializer is not None:
        default_varscope[0].set_initializer(initializer)
      if regularizer is not None:
        default_varscope[0].set_regularizer(regularizer)
      if caching_device is not None:
        default_varscope[0].set_caching_device(caching_device)
      yield default_varscope[0]
    else:
      # Handler for the case when we just prolong current variable scope.
      #   VariableScope with name extended by the provided one, and inherited
      #   reuse and initializer (except if the user provided values to set).
      new_name = old.name + "/" + name_or_scope if old.name else name_or_scope
      default_varscope[0] = VariableScope(
          reuse, name=new_name,
          initializer=old.initializer,
          regularizer=old.regularizer,
          caching_device=old.caching_device,
          name_scope=name_or_scope)
      if initializer is not None:
        default_varscope[0].set_initializer(initializer)
      if regularizer is not None:
        default_varscope[0].set_regularizer(regularizer)
      if caching_device is not None:
        default_varscope[0].set_caching_device(caching_device)
      yield default_varscope[0]
  finally:
    default_varscope[0] = old


# pylint: disable=g-doc-return-or-yield
@contextlib.contextmanager
def variable_scope(name_or_scope, reuse=None, initializer=None,
                   regularizer=None, caching_device=None):
  """Returns a context for variable scope.

  Variable scope allows to create new variables and to share already created
  ones while providing checks to not create or share by accident. For details,
  see the [Variable Scope How To](../../how_tos/variable_scope/index.md),
  here we present only a few basic examples.

  Simple example of how to create a new variable:

  ```python
  with tf.variable_scope("foo"):
      with tf.variable_scope("bar"):
          v = tf.get_variable("v", [1])
          assert v.name == "foo/bar/v:0"
  ```

  Basic example of sharing a variable:

  ```python
  with tf.variable_scope("foo"):
      v = tf.get_variable("v", [1])
  with tf.variable_scope("foo", reuse=True):
      v1 = tf.get_variable("v", [1])
  assert v1 == v
  ```

  Sharing a variable by capturing a scope and setting reuse:

  ```python
  with tf.variable_scope("foo") as scope:
      v = tf.get_variable("v", [1])
      scope.reuse_variables()
      v1 = tf.get_variable("v", [1])
  assert v1 == v
  ```

  To prevent accidental sharing of variables, we raise an exception when
  getting an existing variable in a non-reusing scope.

  ```python
  with tf.variable_scope("foo"):
      v = tf.get_variable("v", [1])
      v1 = tf.get_variable("v", [1])
      #  Raises ValueError("... v already exists ...").
  ```

  Similarly, we raise an exception when trying to get a variable that
  does not exist in reuse mode.

  ```python
  with tf.variable_scope("foo", reuse=True):
      v = tf.get_variable("v", [1])
      #  Raises ValueError("... v does not exists ...").
  ```

  Note that the `reuse` flag is inherited: if we open a reusing scope,
  then all its sub-scopes become reusing as well.

  Args:
    name_or_scope: `string` or `VariableScope`: the scope to open.
    reuse: `True` or `None`; if `True`, we go into reuse mode for this scope as
      well as all sub-scopes; if `None`, we just inherit the parent scope reuse.
    initializer: default initializer for variables within this scope.
    regularizer: default regularizer for variables within this scope.
    caching_device: default caching device for variables within this scope.

  Returns:
    A scope that can be to captured and reused.

  Raises:
    ValueError: when trying to reuse within a create scope, or create within
      a reuse scope, or if reuse is not `None` or `True`.
    TypeError: when the types of some arguments are not appropriate.
  """
  if not isinstance(name_or_scope, (VariableScope,) + six.string_types):
    raise TypeError("VariableScope: name_scope must be a string or "
                    "VariableScope.")
  if isinstance(name_or_scope, six.string_types):
    name = name_or_scope
  else:
    name = name_or_scope._name_scope  # pylint: disable=protected-access
  if name:
    with ops.name_scope(name), _pure_variable_scope(
        name_or_scope, reuse=reuse, initializer=initializer,
        regularizer=regularizer, caching_device=caching_device) as vs:
      yield vs
  else:
    # This can only happen if someone is entering the root variable scope.
    with _pure_variable_scope(
        name_or_scope, reuse=reuse, initializer=initializer,
        regularizer=regularizer, caching_device=caching_device) as vs:
      yield vs


# pylint: disable=g-doc-return-or-yield
@contextlib.contextmanager
def variable_op_scope(values, name_or_scope, default_name, initializer=None,
                      regularizer=None, caching_device=None, reuse=None):
  """Returns a context manager for defining an op that creates variables.

  This context manager validates that the given `values` are from the
  same graph, ensures that that graph is the default graph, and pushes a
  name scope and a variable scope.

  If `name_or_scope` is not None, it is used as is in the variable scope. If
  `scope` is None, then `default_name` is used.  In that case, if the same name
  has been previously used in the same scope, it will made unique be appending
  `_N` to it.

  This is intended to be used when defining generic ops and so reuse is always
  inherited.

  For example, to define a new Python op called `my_op_with_vars`:

  ```python
  def my_op_with_vars(a, b, scope=None):
    with tf.variable_op_scope([a, b], scope, "MyOp") as scope:
      a = tf.convert_to_tensor(a, name="a")
      b = tf.convert_to_tensor(b, name="b")
      c = tf.get_variable('c')
      # Define some computation that uses `a`, `b`, and `c`.
      return foo_op(..., name=scope)
  ```

  Args:
    values: The list of `Tensor` arguments that are passed to the op function.
    name_or_scope: The name argument that is passed to the op function,
      this name_or_scope is not uniquified in the variable scope.
    default_name: The default name to use if the `name_or_scope` argument is
      `None`, this name will be uniquified.
    initializer: The  default initializer to pass to variable scope.
    regularizer: The default regularizer for variables within this scope.
    caching_device: The default caching device for variables within this scope.
    reuse: `True` or `None`; if `True`, we go into reuse mode for this scope as
      well as all sub-scopes; if `None`, we just inherit the parent scope reuse.


  Returns:
    A context manager for use in defining a Python op.

  Raises:
    ValueError: when trying to reuse within a create scope, or create within
      a reuse scope, or if reuse is not `None` or `True`.
    TypeError: when the types of some arguments are not appropriate.
  """
  if default_name is None:
    raise TypeError("default_name cannot be None")
  g = ops._get_graph_from_inputs(values)  # pylint: disable=protected-access
  with g.as_default():
    if name_or_scope:
      with variable_scope(name_or_scope, reuse=reuse, initializer=initializer,
                          regularizer=regularizer,
                          caching_device=caching_device) as vs:
        yield vs
    else:
      if reuse:
        raise ValueError("reuse=True cannot be used without a name_or_scope")
      with ops.name_scope(default_name) as scope:
        count = len(default_name.split("/"))
        scoped_name = "/".join(scope.split("/")[-count - 1:-1])
        with _pure_variable_scope(
            scoped_name, initializer=initializer,
            regularizer=regularizer, caching_device=caching_device) as vs:
          yield vs
