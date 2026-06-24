/*
 * plc_shm.h — PLCFusion 共享内存 ABI（runner_profile=plc）
 * 功能: /dev/plcfusion mmap 布局与魔数定义
 */
#ifndef PLC_SHM_H
#define PLC_SHM_H

#include <linux/types.h>

#define PLC_SHM_MAGIC  0x504C4346u /* "PLCF" */
#define PLC_SHM_VERSION 1
#define PLC_SHM_SIZE   (64 * 1024)

struct plc_shm_hdr {
	u32 magic;
	u32 version;
	u64 cycles;
	s64 min_ns;
	s64 max_ns;
	u32 flags;
	u32 reserved;
};

struct plc_shm {
	struct plc_shm_hdr hdr;
	u8 payload[PLC_SHM_SIZE - sizeof(struct plc_shm_hdr)];
};

#endif /* PLC_SHM_H */
