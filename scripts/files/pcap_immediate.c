/* LD_PRELOAD shim: force libpcap immediate mode before activation.
 * Works around softflowd 1.1.0 (no immediate mode) on TPACKET_V3 libpcap;
 * see irino/softflowd f5c0f99103. Remove once a softflowd release ships the fix. */
#define _GNU_SOURCE
#include <dlfcn.h>
typedef struct pcap pcap_t;
int pcap_activate(pcap_t *p) {
	int (*real)(pcap_t *) = (int (*)(pcap_t *))dlsym(RTLD_NEXT, "pcap_activate");
	int (*imm)(pcap_t *, int) = (int (*)(pcap_t *, int))dlsym(RTLD_NEXT, "pcap_set_immediate_mode");
	if (imm) imm(p, 1);
	return real(p);
}
