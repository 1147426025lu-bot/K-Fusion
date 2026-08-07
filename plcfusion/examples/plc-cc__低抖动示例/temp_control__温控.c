#include "plc_fixed__定点Q.h"

int g_plc_counter = 0;
plc_fix32_t g_sensor_data[5] = {
	PLC_FIX32(25.5), PLC_FIX32(26.2), PLC_FIX32(27.1),
	PLC_FIX32(28.5), PLC_FIX32(30.0),
};
int g_heater_status = 0;

void plc_cycle(void)
{
	g_plc_counter++;

	plc_fix32_t current_temp = g_sensor_data[g_plc_counter % 5];

	if (plc_fix32_cmp(current_temp, PLC_FIX32(28.0)) > 0) {
		g_heater_status = 0;
	} else if (plc_fix32_cmp(current_temp, PLC_FIX32(26.0)) < 0) {
		g_heater_status = 1;
	}

	if (g_heater_status) {
		printf("Cycle %d: Temp %d.%01d - HEATER ON\n", g_plc_counter,
		       plc_fix32_to_int(current_temp),
		       (int)(((uint64_t)(current_temp & 0xFFFFFFFF) * 10) >> 32));
	} else {
		printf("Cycle %d: Temp %d.%01d - HEATER OFF\n", g_plc_counter,
		       plc_fix32_to_int(current_temp),
		       (int)(((uint64_t)(current_temp & 0xFFFFFFFF) * 10) >> 32));
	}

	void *tmp = malloc(64);
	free(tmp);
}
