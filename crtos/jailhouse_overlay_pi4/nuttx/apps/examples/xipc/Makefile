include $(APPDIR)/Make.defs

PROGNAME  = xipc
PRIORITY  = SCHED_PRIORITY_DEFAULT
STACKSIZE = 4096
MODULE    = $(CONFIG_EXAMPLES_XIPC)

MAINSRC = xipc_main.c

CFLAGS += -I$(APPDIR)/examples/xipc

include $(APPDIR)/Application.mk
