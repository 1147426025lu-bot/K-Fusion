/****************************************************************************
 * apps/examples/xshm/xshm_main.c
 *
 * 跨域共享内存 demo：NuttX(Jailhouse 非根 cell) <-> root cell Linux
 *
 * 经 Jailhouse IVSHMEM 的 rw_section 做最小双向通信：
 *   - 共享基址：guest 虚拟 0x1faf1000（= phys，cell 里以 ROOTSHARED 映射）。
 *     注意 NuttX MMU 的 DRAM0_S0 = [0, 0xFC000000) 已用 MT_NORMAL 覆盖该地址，
 *     所以 inmate 无需改 MMU 即可直接访问。
 *   - cache：NuttX 侧是 cacheable，root 侧（UIO mmap）通常是非 cache，
 *     因此读写前后必须自己做 cache clean / invalidate，否则互相看不到。
 *
 * 约定布局（32 位字，与 root 侧 ivshmem_probe__跨域共享内存.py 一致）：
 *   0x00 ROOT_MAGIC    root 写，inmate 读
 *   0x04 INMATE_SEQ    inmate 每轮 +1（证明 inmate -> root）
 *   0x08 INMATE_ECHO   inmate 回写最近一次读到的 magic（证明 root -> inmate）
 *   0x0C INMATE_STATE  1 = running
 *
 * 用法: xshm [-i <interval_ms>] [-n <iters>]
 *   默认 interval=10ms, iters=12000 (约 120s)
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>

#define XSHM_BASE  0x1faf1000u
#define XSHM_WORDS 16u

/* 内核符号（避免依赖具体头文件的声明位置） */
extern void up_invalidate_dcache(uintptr_t start, uintptr_t end);
extern void up_clean_dcache(uintptr_t start, uintptr_t end);

int main(int argc, FAR char *argv[])
{
  int interval_ms = 10;
  long iters = 12000;
  int i;
  long n;
  uintptr_t base = (uintptr_t)XSHM_BASE;
  uintptr_t end  = base + XSHM_WORDS * 4;
  volatile uint32_t *shm = (volatile uint32_t *)XSHM_BASE;
  uint32_t last_magic = 0;
  uint32_t seq = 0;
  int got = 0;

  for (i = 1; i < argc; i++)
    {
      if (strcmp(argv[i], "-i") == 0 && i + 1 < argc)
        {
          interval_ms = atoi(argv[++i]);
        }
      else if (strcmp(argv[i], "-n") == 0 && i + 1 < argc)
        {
          iters = atol(argv[++i]);
        }
      else
        {
          printf("usage: xshm [-i <ms>] [-n <iters>]\n");
          return 1;
        }
    }

  printf("xshm START base=0x%08lx interval=%dms iters=%ld\n",
         (unsigned long)XSHM_BASE, interval_ms, iters);

  for (n = 0; n < iters; n++)
    {
      uint32_t magic;

      up_invalidate_dcache(base, end);
      magic = shm[0];

      if (magic != last_magic)
        {
          last_magic = magic;
          got = 1;
          printf("xshm: ROOT_MAGIC -> 0x%08x (iter %ld)\n", magic, n);
        }

      seq++;
      shm[1] = seq;
      shm[2] = magic;
      shm[3] = 1;
      up_clean_dcache(base, end);

      if ((n + 1) % 1000 == 0)
        {
          printf("xshm: seq=%lu magic=0x%08x\n",
                 (unsigned long)seq, magic);
        }

      if (interval_ms > 0)
        {
          usleep((useconds_t)interval_ms * 1000);
        }
    }

  printf("xshm DONE seq=%lu last_magic=0x%08x got_root_magic=%d\n",
         (unsigned long)seq, last_magic, got);
  return 0;
}
