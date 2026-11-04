# Interrupt diagnosis

Trace the expected enable, pending, vector, handler, and clear paths in source
before reading target state. Use backend-proven registers and symbols only;
unknown capabilities stay unknown. Compare repeated observations of pending and
active state with the event producer and interrupt mask/priority configuration.

Account for read-to-clear and write-one-to-clear side effects. Do not write an
interrupt register merely to test a hypothesis without explicit authorization.
