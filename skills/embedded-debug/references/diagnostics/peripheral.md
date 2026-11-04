# Peripheral and data anomalies

For ADC, timers, PWM, GPIO, and similar peripherals, trace clock/reset,
initialization, configuration, trigger, conversion/transfer, and consumer paths
in source. Identify scaling, signedness, units, sampling time, and update timing
before declaring a hardware error.

Use only register definitions and side-effect rules confirmed for the selected
target configuration. Reads that clear status or alter a capture state must be
authorized and documented; actuator outputs require a reviewed safe-state plan.
