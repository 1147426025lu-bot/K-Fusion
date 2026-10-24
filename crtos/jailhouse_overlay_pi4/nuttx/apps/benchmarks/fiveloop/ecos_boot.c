/* ecos_boot.c — NuttX init：先拉起 fiveloop shmwait，再进 nsh
 *
 * 本机没有 USB-TTL 时，inmate 独占 mini-UART，Linux 敲不了 nsh。
 * init 里用 task_create 挂一个 IVSHMEM 命令循环，root cell 经 /dev/uio0 下发
 * plan/flat。nsh 仍在（串口接上时还能用）。
 */
#include <nuttx/config.h>

#include <stdio.h>
#include <sched.h>

int fiveloop_main(int argc, FAR char *argv[]);
int nsh_main(int argc, FAR char *argv[]);

int ecos_boot_main(int argc, FAR char *argv[])
{
	FAR char *av[3];
	int tid;

	av[0] = "fiveloop";
	av[1] = "shmwait";
	av[2] = NULL;

	tid = task_create("fiveloopd", 150, 16384, fiveloop_main, av);
	if (tid < 0)
		printf("ecos_boot: task_create fiveloopd failed %d\n", tid);
	else
		printf("ecos_boot: fiveloopd tid=%d (IVSHMEM @ 0x1faf1080)\n", tid);

	return nsh_main(argc, argv);
}
