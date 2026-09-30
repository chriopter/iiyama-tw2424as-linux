// SPDX-License-Identifier: GPL-2.0
/*
 * Minimal freestanding devmem for aarch64 Linux (no libc):
 *   devmem ADDR            read 32-bit register, print 0xXXXXXXXX
 *   devmem ADDR VALUE      write 32-bit value, then read back
 * Built with -nostdlib -static; talks to the kernel via raw syscalls.
 */

typedef unsigned long u64;
typedef unsigned int u32;

#define SYS_openat	56
#define SYS_close	57
#define SYS_write	64
#define SYS_exit	93
#define SYS_munmap	215
#define SYS_mmap	222

#define AT_FDCWD	-100
#define O_RDWR		02
#define O_SYNC		04010000
#define PROT_RW		3
#define MAP_SHARED	1
#define PAGE		4096UL

static long sys6(long n, long a, long b, long c, long d, long e, long f)
{
	register long x8 asm("x8") = n;
	register long x0 asm("x0") = a;
	register long x1 asm("x1") = b;
	register long x2 asm("x2") = c;
	register long x3 asm("x3") = d;
	register long x4 asm("x4") = e;
	register long x5 asm("x5") = f;

	asm volatile("svc #0" : "+r"(x0)
		     : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
		     : "memory");
	return x0;
}

static void out(const char *s, long n)
{
	sys6(SYS_write, 1, (long)s, n, 0, 0, 0);
}

static void die(const char *msg, long n)
{
	sys6(SYS_write, 2, (long)msg, n, 0, 0, 0);
	sys6(SYS_exit, 1, 0, 0, 0, 0, 0);
}

static u64 parse(const char *s)
{
	u64 v = 0;
	int base = 10;

	if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
		base = 16;
		s += 2;
	}
	for (; *s; s++) {
		int d;

		if (*s >= '0' && *s <= '9')
			d = *s - '0';
		else if (*s >= 'a' && *s <= 'f')
			d = *s - 'a' + 10;
		else if (*s >= 'A' && *s <= 'F')
			d = *s - 'A' + 10;
		else
			break;
		v = v * base + d;
	}
	return v;
}

static void print_hex(u32 v)
{
	char buf[11] = "0x00000000\n";
	const char *hex = "0123456789abcdef";

	for (int i = 0; i < 8; i++)
		buf[9 - i] = hex[(v >> (i * 4)) & 0xf];
	out(buf, 11);
}

static int run(int argc, char **argv)
{
	u64 addr, base;
	long fd, map;
	volatile u32 *reg;

	if (argc < 2)
		die("usage: devmem ADDR [VALUE]\n", 27);

	addr = parse(argv[1]);
	base = addr & ~(PAGE - 1);

	fd = sys6(SYS_openat, AT_FDCWD, (long)"/dev/mem", O_RDWR | O_SYNC, 0, 0, 0);
	if (fd < 0)
		die("open /dev/mem failed\n", 21);

	map = sys6(SYS_mmap, 0, PAGE, PROT_RW, MAP_SHARED, fd, base);
	if (map < 0 && map > -4096)
		die("mmap failed\n", 12);

	reg = (volatile u32 *)(map + (addr - base));
	if (argc > 2)
		*reg = (u32)parse(argv[2]);
	print_hex(*reg);

	sys6(SYS_munmap, map, PAGE, 0, 0, 0, 0);
	sys6(SYS_close, fd, 0, 0, 0, 0, 0);
	return 0;
}

void __attribute__((noreturn)) cmain(long *sp)
{
	int argc = (int)sp[0];
	char **argv = (char **)(sp + 1);

	sys6(SYS_exit, run(argc, argv), 0, 0, 0, 0, 0);
	__builtin_unreachable();
}

asm(".global _start\n"
    "_start:\n"
    "	mov x0, sp\n"
    "	bl cmain\n");
