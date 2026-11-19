"""Signal plugins (see base.py). Importing this package registers the built-ins."""

from recaply.signals import audio_energy, chat_rate, motion, shout_laugh  # noqa: F401  (register)
from recaply.signals.base import Signal, SignalContext, all_signals, get_signal, register

__all__ = ["Signal", "SignalContext", "register", "get_signal", "all_signals"]
