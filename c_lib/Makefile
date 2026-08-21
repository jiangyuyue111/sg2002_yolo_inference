# Makefile for preprocess_ops.so
# ===============================
# Builds the C preprocessing acceleration library for SG2002 / StarryOS.
#
# Targets:
#   make              → build for current host (PC testing)
#   make riscv         → cross-compile for StarryOS RISC-V musl
#   make clean
#
# StarryOS toolchain:
#   riscv64-linux-musl-gcc (in /opt/riscv64-linux-musl-cross/bin/)

RISCV_CC      = riscv64-linux-musl-gcc
RISCV_CFLAGS  = -shared -fPIC -O3 -march=rv64gc

HOST_CC       = gcc
HOST_CFLAGS   = -shared -fPIC -O2

SOURCES = preprocess_ops.c
TARGET  = preprocess_ops.so

.PHONY: all riscv clean help

all: $(TARGET)
	@echo "Built for host: $(TARGET)"

$(TARGET): $(SOURCES)
	$(HOST_CC) $(HOST_CFLAGS) -o $@ $^ -lm

riscv:
	$(RISCV_CC) $(RISCV_CFLAGS) -o $(TARGET) $(SOURCES) -lm
	@echo "=== Built for StarryOS RISC-V ==="
	$(RISCV_CC) $(RISCV_CFLAGS) -o $(TARGET) $(SOURCES) -lm 2>&1 \
		&& riscv64-linux-musl-strip $(TARGET) \
		&& ls -la $(TARGET) \
		&& riscv64-linux-musl-nm -D $(TARGET) | grep " T "

clean:
	rm -f $(TARGET)

help:
	@echo "Targets:"
	@echo "  make        build for host (PC test)"
	@echo "  make riscv   cross-compile for StarryOS SG2002"
	@echo "  make clean"
