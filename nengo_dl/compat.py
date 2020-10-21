# pylint: disable=unused-import,ungrouped-imports

"""
Utilities to ease cross-compatibility between different versions of upstream
dependencies.
"""

from collections import OrderedDict
import inspect

import nengo
from nengo._vendor.scipy.sparse import linalg_interface, linalg_onenormest
from packaging import version
import tensorflow as tf
from tensorflow.python.eager import context


class NoType:
    """A type that can never be instantiated, so nothing will be an instance"""

    def __init__(self, *args, **kwargs):  # pragma: no cover
        raise RuntimeError("cannot instantiate")


def make_dummy_type(name):
    """Return a NoType subclass with the given name"""
    return type(name, (NoType,), {})


def is_dummy_type(t):
    """Returns True if the given type is a NoType subclass, False otherwise."""
    return issubclass(t, NoType)


# TensorFlow compatibility


class TFLogFilter:
    """
    Filter for cleaning up the TensorFlow log output.

    Parameters
    ----------
    err_on_deprecation : bool
        If True, treat deprecation log messages as errors.
    """

    def __init__(self, err_on_deprecation):
        self.err_on_deprecation = err_on_deprecation

    def filter(self, record):
        """
        Apply filter to log record.

        Parameters
        ----------
        record : `LogRecord`
            Message emitted by logger.

        Returns
        -------
        pass : bool
            If True, pass the ``record``, otherwise filter it out.

        Raises
        ------
        AttributeError
            If a deprecation message is detected and ``err_on_deprecation=True``.
        """

        # "tf.keras.backend.get_session is deprecated": this deprecation message
        # is raised incorrectly due to a bug, see
        # https://github.com/tensorflow/tensorflow/issues/33182
        if len(record.args) > 1 and record.args[1] == "tf.keras.backend.get_session":
            return False

        # "Output steps_run missing from loss dictionary": steps_run should
        # never have a loss defined
        if record.msg.startswith("Output steps_run missing from loss dictionary"):
            return False

        if self.err_on_deprecation and (
            "deprecation.py" in record.pathname or "deprecated" in record.msg.lower()
        ):
            msg = record.getMessage()
            raise AttributeError("Deprecation warning detected:\n%s" % msg)

        return True


tf.get_logger().addFilter(TFLogFilter(err_on_deprecation=False))

if version.parse(tf.__version__) < version.parse("2.3.0rc0"):

    def _build_map(outputs):
        """
        Vendored from ``tensorflow.python.keras.engine.functional._build_map``
        in TF 2.3.0 (with a few changes noted below).
        """
        finished_nodes = set()
        nodes_in_progress = set()
        nodes_in_decreasing_depth = []  # nodes from inputs -> outputs.
        layer_indices = {}  # layer -> in traversal order.
        for output in tf.nest.flatten(outputs):
            _build_map_helper(
                output,
                finished_nodes,
                nodes_in_progress,
                nodes_in_decreasing_depth,
                layer_indices,
            )

        # CHANGED: add `node.layer` alias for `node.outbound_layer`
        for node in nodes_in_decreasing_depth:
            node.layer = node.outbound_layer

        return nodes_in_decreasing_depth, layer_indices

    def _build_map_helper(
        tensor,
        finished_nodes,
        nodes_in_progress,
        nodes_in_decreasing_depth,
        layer_indices,
    ):
        """Recursive helper for `_build_map`."""
        layer, node_index, _ = tensor._keras_history  # pylint: disable=protected-access
        node = layer._inbound_nodes[node_index]  # pylint: disable=protected-access

        # Don't repeat work for shared subgraphs
        if node in finished_nodes:
            return

        # Prevent cycles.
        if node in nodes_in_progress:  # pragma: no cover
            raise ValueError(
                "The tensor "
                + str(tensor)
                + ' at layer "'
                + layer.name
                + '" is part of a cycle.'
            )

        # Store the traversal order for layer sorting.
        if layer not in layer_indices:
            layer_indices[layer] = len(layer_indices)

        # Propagate to all previous tensors connected to this node.
        nodes_in_progress.add(node)
        # CHANGED: `not node.is_input` to `node.inbound_layers`
        if node.inbound_layers:
            # CHANGED: `node.keras_inputs` to `tf.nest.flatten(node.input_tensors)`
            # CHANGED: `tensor` to `input_tensor` (for pylint)
            for input_tensor in tf.nest.flatten(node.input_tensors):
                _build_map_helper(
                    input_tensor,
                    finished_nodes,
                    nodes_in_progress,
                    nodes_in_decreasing_depth,
                    layer_indices,
                )

        finished_nodes.add(node)
        nodes_in_progress.remove(node)
        nodes_in_decreasing_depth.append(node)


else:
    from tensorflow.python.keras.engine.functional import _build_map


if version.parse(tf.__version__) < version.parse("2.3.0rc0"):
    from tensorflow.python.keras.engine import network

    # monkeypatch to fix bug in TF2.2, see
    # https://github.com/tensorflow/tensorflow/issues/37548
    old_conform = network.Network._conform_to_reference_input

    def _conform_to_reference_input(self, tensor, ref_input):
        keras_history = getattr(tensor, "_keras_history", None)

        tensor = old_conform(self, tensor, ref_input)

        if keras_history is not None:
            tensor._keras_history = keras_history

        return tensor

    network.Network._conform_to_reference_input = _conform_to_reference_input


# Nengo compatibility

# monkeypatch fix for https://github.com/nengo/nengo/pull/1587
linalg_onenormest.aslinearoperator = linalg_interface.aslinearoperator

ConvolutionTranspose = make_dummy_type("ConvolutionTranspose")
ConvTransposeInc = make_dummy_type("ConvTransposeInc")

if version.parse(nengo.__version__) < version.parse("3.1.0.dev0"):
    PoissonSpiking = make_dummy_type("PoissonSpiking")
    RegularSpiking = make_dummy_type("RegularSpiking")
    StochasticSpiking = make_dummy_type("StochasticSpiking")
    Tanh = make_dummy_type("Tanh")
    NoTransform = make_dummy_type("NoTransform")

    default_transform = 1

    def conn_has_weights(conn):
        """All connections have weights."""
        return True

    def neuron_state(neuron_op):
        """Look up keys from function signature."""
        names = list(inspect.signature(neuron_op.neurons.step_math).parameters.keys())[
            3:
        ]
        assert len(names) == len(neuron_op.states)
        return OrderedDict((n, s) for n, s in zip(names, neuron_op.states))

    def neuron_step(neuron_op, dt, J, output, state):  # pragma: no cover (runs in TF)
        """Call step_math instead of step."""
        neuron_op.neurons.step_math(dt, J, output, *state.values())

    def to_neurons(conn):
        """Check whether the output of a connection is a neuron object."""
        return isinstance(conn.post_obj, nengo.ensemble.Neurons) or (
            isinstance(conn.pre_obj, nengo.Ensemble)
            and isinstance(conn.post_obj, nengo.Ensemble)
            and conn.solver.weights
        )


else:
    from nengo.neurons import PoissonSpiking, RegularSpiking, StochasticSpiking, Tanh
    from nengo.transforms import NoTransform

    try:
        from nengo.transforms import ConvolutionTranspose
        from nengo.builder.transforms import ConvTransposeInc
    except ImportError:  # pragma: no cover
        ConvolutionTranspose = make_dummy_type("ConvolutionTranspose")
        ConvTransposeInc = make_dummy_type("ConvTransposeInc")

    default_transform = None

    def conn_has_weights(conn):
        """Equivalent to conn.has_weights."""
        return conn.has_weights

    def neuron_state(neuron_op):
        """Equivalent to neuron_op.state."""
        return neuron_op.state

    def neuron_step(neuron_op, dt, J, output, state):  # pragma: no cover (runs in TF)
        """Equivalent to neuron_op.step."""
        neuron_op.neurons.step(dt, J, output, **state)

    def to_neurons(conn):
        """Equivalent to conn._to_neurons."""
        return conn._to_neurons


def eager_enabled():
    """
    Check if we're in eager mode or graph mode.

    Note: this function differs from ``tf.executing_eagerly()`` in that it will still
    return ``True`` if we're inside a ``tf.function``. Essentially this checks whether
    the user has called ``tf.compat.v1.disable_eager_execution()`` or not.
    """

    return context.default_execution_mode == context.EAGER_MODE


try:
    from keras_spiking import SpikingActivation, Lowpass, Alpha
except ImportError:
    SpikingActivation = make_dummy_type("keras_spiking_SpikingActivation")
    Lowpass = make_dummy_type("keras_spiking_Lowpass")
    Alpha = make_dummy_type("keras_spiking_Alpha")
