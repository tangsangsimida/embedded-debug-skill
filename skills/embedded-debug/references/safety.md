# Safety

Read-only inspection is the default. Obtain explicit user authorization immediately before flashing/erasing, reset, memory/register writes, starting potentially actuator-controlling firmware, or bulk hardware tests. `--authorize` is an explicit CLI confirmation flag, not a substitute for informed authorization. Record target-affecting actions. Never disable interlocks, emergency stops, or protection logic. Do not expose OpenOCD on a network interface unless the user specifically requests and understands it.
