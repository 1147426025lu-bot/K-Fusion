/* ecos_shm_cmd.h — fiveloop 跨域命令槽（Jailhouse IVSHMEM rw_section）
 *
 * 基址与 xshm 相同：phys/virt 0x1faf1000。本协议从偏移 0x80 起，
 * 不覆盖 xshm 的前 16 字节（ROOT_MAGIC / SEQ / ECHO / STATE）。
 *
 * Linux（uio_ivshmem map2）写 cmd，NuttX inmate 跑完回写结果。
 * 这是给「本机没有 USB-TTL、mini-UART 已被 inmate 独占」时用的通道，
 * 不是 feat(ivshmem) 的正式 SPSC 数据面。
 *
 * 字序：小端 32-bit。cmd 必须最后写。
 */
#ifndef ECOS_SHM_CMD_H
#define ECOS_SHM_CMD_H

#define ECOS_SHM_BASE     0x1faf1000u
#define ECOS_SHM_OFF      0x80u
#define ECOS_SHM_MAGIC    0xEC05F10Cu
#define ECOS_SHM_WORDS    16u
#define ECOS_PROTO_V1     1u

#define ECOS_CMD_IDLE     0u
#define ECOS_CMD_RUN      1u

#define ECOS_MODE_PLAN    0u
#define ECOS_MODE_FLAT    1u

#define ECOS_ST_IDLE      0u
#define ECOS_ST_RUNNING   1u
#define ECOS_ST_DONE      2u
#define ECOS_ST_ERROR     3u

/* 相对 ECOS_SHM_BASE+ECOS_SHM_OFF 的字下标 */
#define ECOS_W_MAGIC      0
#define ECOS_W_CMD        1
#define ECOS_W_MODE       2
#define ECOS_W_CYCLES     3
#define ECOS_W_DIGEST     4
#define ECOS_W_RESP_P50   5
#define ECOS_W_RESP_MAX   6
#define ECOS_W_START_P50  7
#define ECOS_W_MISS       8
#define ECOS_W_STATUS     9
#define ECOS_W_EXEC_P50   10
#define ECOS_W_NREL       11
#define ECOS_W_CMD_SEQ    12
#define ECOS_W_RESULT_SEQ 13
#define ECOS_W_ERROR      14
#define ECOS_W_ALIVE      15
#define ECOS_ALIVE        0xF100D00Du

#endif
