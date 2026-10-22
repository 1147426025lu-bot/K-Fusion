#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""closure_audit__依赖闭包审计.py — E3 用

对一个目标文件做「OS 依赖面」审计：把未定义符号分成三类，输出 markdown 表。

分类口径：
  PLC    被内核化重映射到 plc_* 抽象层的（后端提供，与 OS 无关）
  PURE   纯计算/libc 工具（memset/strcmp/strtol…），任何 libc 都有，不碰内核对象
  OS     真正触碰操作系统语义的（时间、线程、文件、打印…）
  OTHER  未归类（出现即为可疑，需人工判断）

用法：
  python3 closure_audit__依赖闭包审计.py --nm aarch64-none-elf-nm \
      "cyclictest:/path/to/cyclictest.o" "bm2pid:/path/to/bm2.o" ...
  不加 --nm 则用环境里的 nm。
"""
import subprocess
import sys
from pathlib import Path

# --- 分类表 ---------------------------------------------------------------
PURE = {
    "memset", "memcpy", "memmove", "memcmp", "strlen", "strcmp", "strncmp",
    "strncasecmp", "strcpy", "strncpy", "strcat", "strchr", "strrchr",
    "strstr", "strtok", "strtol", "strtoul", "strtoll", "atoi", "atof",
    "snprintf", "sprintf", "vsnprintf", "abs", "labs", "llabs", "bsearch",
    "qsort", "isalpha", "isdigit", "isspace", "toupper", "tolower",
    "__adddf3", "__subdf3", "__muldf3", "__divdf3", "__fixdfdi", "__fixdfsi",
    "__floatdidf", "__floatundidf", "__divdi3", "__udivdi3", "__moddi3",
}

OS = {
    "clock_gettime", "clock_nanosleep", "clock_settime", "clock_getres",
    "nanosleep", "usleep", "sleep", "gettimeofday", "setitimer", "alarm",
    "pause", "open", "close", "read", "write", "ioctl", "poll", "select",
    "lseek", "fsync", "unlink", "mkfifo", "shm_open", "mmap", "munmap",
    "fopen", "fclose", "fdopen", "printf", "fprintf", "vfprintf", "puts",
    "putchar", "fputc", "perror", "fflush", "fscanf", "fread", "fwrite", "fputs",
    "pthread_create", "pthread_join", "pthread_self", "pthread_detach",
    "pthread_setschedparam", "pthread_setaffinity_np", "pthread_attr_init",
    "sched_setaffinity", "sched_getaffinity", "sched_setscheduler",
    "sched_getparam", "sched_getscheduler", "sched_get_priority_max",
    "getpid", "gettid", "getrlimit", "setrlimit", "sysconf", "uname",
    "mlock", "mlockall", "munlockall", "malloc", "calloc", "realloc", "free",
    "execl", "fork", "waitpid", "kill", "raise", "signal", "sigaction",
    "__errno", "__errno_location", "atexit", "getenv", "exit", "abort",
    "getopt", "getopt_long", "getoptargp", "getoptindp", "getopterrp",
    "getoptoptp", "lib_get_stream",
}


def nm_undef(nm, obj):
    out = subprocess.run([nm, "-u", obj], capture_output=True, text=True)
    syms = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts:
            syms.add(parts[-1])
    return sorted(syms)


def classify(sym):
    if sym.startswith("plc_"):
        return "PLC"
    if sym.startswith("llvm.") or sym.startswith("__isoc"):
        return "PURE"
    if sym in PURE:
        return "PURE"
    if sym in OS:
        return "OS"
    return "OTHER"


def main():
    args = sys.argv[1:]
    nm = "nm"
    if args and args[0] == "--nm":
        nm = args[1]
        args = args[2:]
    if not args:
        raise SystemExit(__doc__)

    rows = []
    for spec in args:
        name, path = spec.split(":", 1)
        if not Path(path).exists():
            print(f"!! 找不到 {path}", file=sys.stderr)
            continue
        syms = nm_undef(nm, path)
        cls = {s: classify(s) for s in syms}
        rows.append((name, path, syms, cls))

    print("| 负载 | 闭包大小 | PLC | PURE | OS | 未归类 |")
    print("|---|---|---|---|---|---|")
    for name, path, syms, cls in rows:
        n = lambda k: sum(1 for s in syms if cls[s] == k)
        print(f"| {name} | **{len(syms)}** | {n('PLC')} | {n('PURE')} | {n('OS')} | {n('OTHER')} |")

    print()
    for name, path, syms, cls in rows:
        print(f"### {name}")
        for k in ("PLC", "PURE", "OS", "OTHER"):
            group = [s for s in syms if cls[s] == k]
            if group:
                print(f"- **{k}** ({len(group)}): " + ", ".join(group))
        print()


if __name__ == "__main__":
    main()
