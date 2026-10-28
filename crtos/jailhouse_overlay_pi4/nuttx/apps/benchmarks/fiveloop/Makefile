include $(APPDIR)/Make.defs

MODULE    = $(CONFIG_BENCHMARK_FIVELOOP)

# This inmate is launched directly by CONFIG_INIT_ENTRYPOINT.  Keep both
# translation units in libapps.a instead of registering shell builtins.
CSRCS = fiveloop_main.c ecos_boot.c

include $(APPDIR)/Application.mk
