// SPDX-License-Identifier: GPL-2.0
/*
 * rebootmode MODE: sync, then reboot(LINUX_REBOOT_CMD_RESTART2, MODE) directly.
 * The kernel's syscon-reboot-mode driver stores the one-shot boot mode in
 * PMUGRF+0x300 for the vendor U-Boot. Unlike Android's "reboot recovery" this
 * never writes a persistent command into the misc partition.
 * Freestanding aarch64, no libc.
 */
#define SYS_sync	81
#define SYS_reboot	142
#define SYS_write	64
#define SYS_exit	93

static long sys4(long n, long a, long b, long c, long d)
{
	register long x8 asm("x8") = n;
	register long x0 asm("x0") = a;
	register long x1 asm("x1") = b;
	register long x2 asm("x2") = c;
	register long x3 asm("x3") = d;

	asm volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3) : "memory");
	return x0;
}

void __attribute__((noreturn)) cmain(long *sp)
{
	char **argv = (char **)(sp + 1);

	if (sp[0] < 2) {
		sys4(SYS_write, 2, (long)"usage: rebootmode MODE\n", 23, 0);
		sys4(SYS_exit, 1, 0, 0, 0);
	}
	sys4(SYS_sync, 0, 0, 0, 0);
	sys4(SYS_reboot, 0xfee1dead, 0x28121969, 0xa1b2c3d4, (long)argv[1]);
	sys4(SYS_write, 2, (long)"reboot failed\n", 14, 0);
	sys4(SYS_exit, 1, 0, 0, 0);
	__builtin_unreachable();
}

asm(".global _start\n_start:\n	mov x0, sp\n	bl cmain\n");
