/*
 * rt_periodic_stats.c — 多 TU 辅助：最坏周期采样（供 PLCFusion llvm-link 演示）
 */
long rt_periodic_record_worst(long prev, long cur)
{
	return cur > prev ? cur : prev;
}
