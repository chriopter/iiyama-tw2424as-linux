// SPDX-License-Identifier: MIT
/*
 * wallpanel-gamma: hold the wlr-gamma-control of one (or every) Wayland output and set the colour
 * temperature read from stdin, one integer Kelvin value per line. The control is kept for the whole
 * run, so a new temperature replaces the ramp in place (no identity flash as with restarting wlsunset).
 *
 *   wallpanel-gamma [-o NAME] [-f MS] [-v]
 *
 * Exit 0 on stdin EOF (control released: the compositor restores the identity ramp), 1 on any
 * compositor-side failure (no gamma manager, output missing, control failed/held by another client,
 * connection lost), 2 on bad usage. See README.md.
 *
 * Colour math (whitepoint, ramp) is copied from wlsunset 0.4.0 (color_math.c, main.c) with gamma 1.0,
 * so a given K gives exactly the ramp wlsunset sets for that K:
 *
 *   Copyright 2020 Kenny Levinsen
 *   Copyright 2026 chriopter
 *
 *   Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
 *   associated documentation files (the "Software"), to deal in the Software without restriction,
 *   including without limitation the rights to use, copy, modify, merge, publish, distribute,
 *   sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
 *   furnished to do so, subject to the following conditions:
 *
 *   The above copyright notice and this permission notice shall be included in all copies or
 *   substantial portions of the Software.
 *
 *   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
 *   NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 *   NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
 *   DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
 *   OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <poll.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include "wlr-gamma-control-unstable-v1-client-protocol.h"

#ifndef VERSION
#define VERSION "unknown"
#endif

#define KELVIN_MIN 1000
#define KELVIN_MAX 25000
#define IDENTITY_K 6500
#define FADE_STEP_MS 20
#define GAIN_MIN 50  /* %, red/blue gain */

/* ---- colour math: wlsunset 0.4.0 color_math.c (calc_whitepoint and helpers), unchanged ---- */

struct rgb { double r, g, b; };
struct xyz { double x, y, z; };

static int illuminant_d(int temp, double *x, double *y)
{
	if (temp >= 2500 && temp <= 7000) {
		*x = 0.244063 + 0.09911e3 / temp + 2.9678e6 / pow(temp, 2) - 4.6070e9 / pow(temp, 3);
	} else if (temp > 7000 && temp <= 25000) {
		*x = 0.237040 + 0.24748e3 / temp + 1.9018e6 / pow(temp, 2) - 2.0064e9 / pow(temp, 3);
	} else {
		errno = EINVAL;
		return -1;
	}
	*y = (-3 * pow(*x, 2)) + (2.870 * (*x)) - 0.275;
	return 0;
}

static int planckian_locus(int temp, double *x, double *y)
{
	if (temp >= 1667 && temp <= 4000) {
		*x = -0.2661239e9 / pow(temp, 3) - 0.2343589e6 / pow(temp, 2) + 0.8776956e3 / temp + 0.179910;
		if (temp <= 2222)
			*y = -1.1064814 * pow(*x, 3) - 1.34811020 * pow(*x, 2) + 2.18555832 * (*x) - 0.20219683;
		else
			*y = -0.9549476 * pow(*x, 3) - 1.37418593 * pow(*x, 2) + 2.09137015 * (*x) - 0.16748867;
	} else if (temp > 4000 && temp < 25000) {
		*x = -3.0258469e9 / pow(temp, 3) + 2.1070379e6 / pow(temp, 2) + 0.2226347e3 / temp + 0.240390;
		*y = 3.0817580 * pow(*x, 3) - 5.87338670 * pow(*x, 2) + 3.75112997 * (*x) - 0.37001483;
	} else {
		errno = EINVAL;
		return -1;
	}
	return 0;
}

static double srgb_gamma(double value, double gamma)
{
	if (value <= 0.0031308)
		return 12.92 * value;
	return pow(1.055 * value, 1.0 / gamma) - 0.055;
}

static double clamp(double value)
{
	return value > 1.0 ? 1.0 : value < 0.0 ? 0.0 : value;
}

static struct rgb xyz_to_srgb(const struct xyz *xyz)
{
	return (struct rgb){
		.r = srgb_gamma(clamp(3.2404542 * xyz->x - 1.5371385 * xyz->y - 0.4985314 * xyz->z), 2.2),
		.g = srgb_gamma(clamp(-0.9692660 * xyz->x + 1.8760108 * xyz->y + 0.0415560 * xyz->z), 2.2),
		.b = srgb_gamma(clamp(0.0556434 * xyz->x - 0.2040259 * xyz->y + 1.0572252 * xyz->z), 2.2),
	};
}

static void srgb_normalize(struct rgb *rgb)
{
	double maxw = fmax(rgb->r, fmax(rgb->g, rgb->b));
	rgb->r /= maxw;
	rgb->g /= maxw;
	rgb->b /= maxw;
}

static struct rgb whitepoint(int temp)
{
	if (temp == 6500)
		return (struct rgb){ .r = 1.0, .g = 1.0, .b = 1.0 };
	struct xyz wp;
	if (temp >= 25000) {
		illuminant_d(25000, &wp.x, &wp.y);
	} else if (temp >= 4000) {
		illuminant_d(temp, &wp.x, &wp.y);
	} else if (temp >= 2500) {
		double x1, y1, x2, y2;
		illuminant_d(temp, &x1, &y1);
		planckian_locus(temp, &x2, &y2);
		double factor = (4000. - temp) / 1500.;
		double sinefactor = (cos(M_PI * factor) + 1.0) / 2.0;
		wp.x = x1 * sinefactor + x2 * (1.0 - sinefactor);
		wp.y = y1 * sinefactor + y2 * (1.0 - sinefactor);
	} else {
		planckian_locus(temp >= 1667 ? temp : 1667, &wp.x, &wp.y);
	}
	wp.z = 1.0 - wp.x - wp.y;
	struct rgb wp_rgb = xyz_to_srgb(&wp);
	srgb_normalize(&wp_rgb);
	return wp_rgb;
}

/* Colour balance ("K R B" on stdin, % of red and blue, 50-100): matches the panel's white to a bulb of the
 * same temperature (LED bulbs are greener than this panel's white). Full below 4000 K, fading out towards
 * 6500 K, so neutral stays the identity ramp. */
static int gain_r = 100, gain_b = 100;

/* wlsunset main.c fill_gamma_table(), gamma fixed to 1.0 (wlsunset's default) */
static void fill_ramp(uint16_t *table, uint32_t size, int temp)
{
	const double gamma = 1.0;
	struct rgb wp = whitepoint(temp);
	if ((gain_r < 100 || gain_b < 100) && temp < IDENTITY_K) {
		double f = temp <= 4000 ? 1.0 : (IDENTITY_K - temp) / (double)(IDENTITY_K - 4000);
		wp.r *= 1.0 - (100 - gain_r) / 100.0 * f;
		wp.b *= 1.0 - (100 - gain_b) / 100.0 * f;
	}
	uint16_t *r = table, *g = table + size, *b = table + 2 * size;
	for (uint32_t i = 0; i < size; ++i) {
		double val = (double)i / (size - 1);
		r[i] = (uint16_t)(UINT16_MAX * pow(val * wp.r, 1.0 / gamma));
		g[i] = (uint16_t)(UINT16_MAX * pow(val * wp.g, 1.0 / gamma));
		b[i] = (uint16_t)(UINT16_MAX * pow(val * wp.b, 1.0 / gamma));
	}
}

/* ---- Wayland ---- */

struct output {
	struct wl_list link;
	struct wl_output *wl_output;
	struct zwlr_gamma_control_v1 *control;
	uint32_t global, version, ramp_size;
	char *name;
	bool selected, failed;
};

static struct wl_display *display;
static struct zwlr_gamma_control_manager_v1 *manager;
static struct wl_list outputs;
static const char *want_output;
static bool verbose, started;

static void msg(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	fputs("wallpanel-gamma: ", stderr);
	vfprintf(stderr, fmt, ap);
	fputc('\n', stderr);
	va_end(ap);
}

static void die(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	fputs("wallpanel-gamma: error: ", stderr);
	vfprintf(stderr, fmt, ap);
	fputc('\n', stderr);
	va_end(ap);
	exit(1);
}

static const char *oname(const struct output *o)
{
	return o->name ? o->name : "(unnamed output)";
}

static void gamma_size(void *data, struct zwlr_gamma_control_v1 *c, uint32_t size)
{
	(void)c;
	struct output *o = data;
	o->ramp_size = size;
}

static void gamma_failed(void *data, struct zwlr_gamma_control_v1 *c)
{
	(void)c;
	struct output *o = data;
	o->failed = true;
	if (started)  /* after startup, main loop exits on it */
		msg("%s: gamma control failed", oname(o));
}

static const struct zwlr_gamma_control_v1_listener gamma_listener = {
	.gamma_size = gamma_size,
	.failed = gamma_failed,
};

static void out_geometry(void *d, struct wl_output *w, int32_t x, int32_t y, int32_t pw, int32_t ph,
	int32_t sp, const char *make, const char *model, int32_t t)
{
	(void)d, (void)w, (void)x, (void)y, (void)pw, (void)ph, (void)sp, (void)make, (void)model, (void)t;
}
static void out_mode(void *d, struct wl_output *w, uint32_t f, int32_t wd, int32_t h, int32_t r)
{
	(void)d, (void)w, (void)f, (void)wd, (void)h, (void)r;
}
static void out_done(void *d, struct wl_output *w) { (void)d, (void)w; }
static void out_scale(void *d, struct wl_output *w, int32_t s) { (void)d, (void)w, (void)s; }
static void out_name(void *data, struct wl_output *w, const char *name)
{
	(void)w;
	struct output *o = data;
	free(o->name);
	o->name = strdup(name);
}
static void out_description(void *d, struct wl_output *w, const char *s) { (void)d, (void)w, (void)s; }

static const struct wl_output_listener output_listener = {
	.geometry = out_geometry,
	.mode = out_mode,
	.done = out_done,
	.scale = out_scale,
	.name = out_name,
	.description = out_description,
};

static void registry_global(void *data, struct wl_registry *reg, uint32_t global, const char *iface,
	uint32_t version)
{
	(void)data;
	if (strcmp(iface, wl_output_interface.name) == 0) {
		if (started)  /* outputs appearing later are not handled: restart picks them up */
			return;
		struct output *o = calloc(1, sizeof(*o));
		if (!o)
			die("out of memory");
		o->global = global;
		o->version = version < 4 ? version : 4;
		o->wl_output = wl_registry_bind(reg, global, &wl_output_interface, o->version);
		wl_output_add_listener(o->wl_output, &output_listener, o);
		wl_list_insert(outputs.prev, &o->link);
	} else if (strcmp(iface, zwlr_gamma_control_manager_v1_interface.name) == 0) {
		manager = wl_registry_bind(reg, global, &zwlr_gamma_control_manager_v1_interface, 1);
	}
}

static void registry_global_remove(void *data, struct wl_registry *reg, uint32_t global)
{
	(void)data, (void)reg;
	struct output *o;
	wl_list_for_each(o, &outputs, link)
		if (o->global == global && o->selected)
			die("%s: output removed", oname(o));
}

static const struct wl_registry_listener registry_listener = {
	.global = registry_global,
	.global_remove = registry_global_remove,
};

/* Send the ramp for temp to every selected output. Each set_gamma gets a fresh memfd, so a table
 * still being read by the compositor is never rewritten. */
static void apply(int temp)
{
	struct output *o;
	wl_list_for_each(o, &outputs, link) {
		if (!o->selected)
			continue;
		size_t len = (size_t)o->ramp_size * 3 * sizeof(uint16_t);
		uint16_t *table = malloc(len);
		if (!table)
			die("out of memory");
		fill_ramp(table, o->ramp_size, temp);
		int fd = memfd_create("wallpanel-gamma", MFD_CLOEXEC);
		if (fd < 0)
			die("memfd_create: %s", strerror(errno));
		for (size_t off = 0; off < len;) {
			ssize_t n = write(fd, (char *)table + off, len - off);
			if (n < 0 && errno == EINTR)
				continue;
			if (n <= 0)
				die("writing the gamma table: %s", strerror(errno));
			off += (size_t)n;
		}
		free(table);
		lseek(fd, 0, SEEK_SET);
		zwlr_gamma_control_v1_set_gamma(o->control, fd);  /* libwayland dups the fd */
		close(fd);
	}
	if (wl_display_flush(display) < 0 && errno != EAGAIN)
		die("compositor connection lost: %s", strerror(errno));
}

static int64_t now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void check_failed(void)
{
	struct output *o;
	wl_list_for_each(o, &outputs, link)
		if (o->selected && o->failed)
			die("%s: gamma control failed (held by another client, or the output lost it)", oname(o));
}

static void release_and_exit(void)
{
	struct output *o;
	wl_list_for_each(o, &outputs, link)
		if (o->control)
			zwlr_gamma_control_v1_destroy(o->control);
	wl_display_roundtrip(display);
	wl_display_disconnect(display);
	msg("stdin closed, gamma control released");
	exit(0);
}

/* parse one input line "K" or "K R B" (red/blue gain in %, sets *r, *b): returns K, 0 for an empty line,
 * -1 if invalid */
static int parse_line(char *s, int *r, int *b)
{
	while (*s == ' ' || *s == '\t' || *s == '\r')
		s++;
	char *e = s + strlen(s);
	while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r'))
		*--e = 0;
	if (!*s)
		return 0;
	char *end;
	errno = 0;
	long k = strtol(s, &end, 10);
	if (errno || k < KELVIN_MIN || k > KELVIN_MAX)
		return -1;
	if (*end) {
		char *e2, *e3;
		long vr = strtol(end, &e2, 10);
		long vb = strtol(e2, &e3, 10);
		if (errno || *e3 || e2 == end || e3 == e2 || vr < GAIN_MIN || vr > 100 || vb < GAIN_MIN || vb > 100)
			return -1;
		*r = (int)vr;
		*b = (int)vb;
	}
	return (int)k;
}

static void usage(FILE *f)
{
	fprintf(f,
		"usage: wallpanel-gamma [-o NAME] [-f MS] [-v]\n"
		"  reads colour temperatures (integer Kelvin %d-%d, one per line, optionally followed by red\n"
		"  and blue gain %d-100 %%, e.g. \"3300 85 100\") from stdin and sets the gamma ramp (wlsunset\n"
		"  colour math, %d K = identity); exits 0 on EOF.\n"
		"  -o NAME  only this output (wl_output name, e.g. DSI-1); default: all outputs\n"
		"  -f MS    fade to each new temperature over MS milliseconds (default 0: jump)\n"
		"  -v       log every applied step\n"
		"  -V       print the version\n",
		KELVIN_MIN, KELVIN_MAX, GAIN_MIN, IDENTITY_K);
}

int main(int argc, char **argv)
{
	long fade_ms = 0;
	int c;
	while ((c = getopt(argc, argv, "o:f:vVh")) != -1) {
		char *end;
		switch (c) {
		case 'o': want_output = optarg; break;
		case 'f':
			errno = 0;
			fade_ms = strtol(optarg, &end, 10);
			if (errno || *end || !*optarg || fade_ms < 0 || fade_ms > 3600000) {
				fprintf(stderr, "wallpanel-gamma: -f needs milliseconds (0-3600000)\n");
				return 2;
			}
			break;
		case 'v': verbose = true; break;
		case 'V': printf("wallpanel-gamma %s\n", VERSION); return 0;
		case 'h': usage(stdout); return 0;
		default: usage(stderr); return 2;
		}
	}
	if (optind != argc) {
		usage(stderr);
		return 2;
	}

	wl_list_init(&outputs);
	display = wl_display_connect(NULL);
	if (!display)
		die("cannot connect to the Wayland compositor (WAYLAND_DISPLAY/XDG_RUNTIME_DIR)");
	struct wl_registry *reg = wl_display_get_registry(display);
	wl_registry_add_listener(reg, &registry_listener, NULL);
	if (wl_display_roundtrip(display) < 0)
		die("compositor connection lost");
	if (!manager)
		die("compositor has no gamma control manager (zwlr_gamma_control_manager_v1)");
	if (wl_display_roundtrip(display) < 0)  /* wl_output name events */
		die("compositor connection lost");

	struct output *o;
	int n = 0;
	wl_list_for_each(o, &outputs, link) {
		if (want_output) {
			if (o->version < 4)
				die("wl_output version %u has no names, cannot select -o %s", o->version, want_output);
			if (!o->name || strcmp(o->name, want_output) != 0)
				continue;
		}
		o->selected = true;
		o->control = zwlr_gamma_control_manager_v1_get_gamma_control(manager, o->wl_output);
		zwlr_gamma_control_v1_add_listener(o->control, &gamma_listener, o);
		n++;
	}
	if (!n)
		die(want_output ? "output %s not found" : "no outputs%s", want_output ? want_output : "");
	if (wl_display_roundtrip(display) < 0)  /* gamma_size or failed */
		die("compositor connection lost");
	check_failed();
	wl_list_for_each(o, &outputs, link) {
		if (!o->selected)
			continue;
		if (o->ramp_size < 2)
			die("%s: gamma ramp size %u (output without gamma LUT?)", oname(o), o->ramp_size);
		msg("%s: gamma control acquired, ramp size %u", oname(o), o->ramp_size);
	}
	started = true;

	char buf[256];
	size_t used = 0;
	bool eof = false, discard = false;
	/* cur: temperature on screen. Before our first set_gamma the compositor shows identity. */
	int cur = IDENTITY_K, from = IDENTITY_K, target = IDENTITY_K;
	bool reapply = false;  /* the colour balance changed */
	int64_t fade_start = 0;

	for (;;) {
		/* fade step or jump */
		int timeout = -1;
		if (target != cur || reapply) {
			int k = target;
			if (fade_ms > 0) {
				int64_t t = now_ms() - fade_start;
				if (t < fade_ms) {
					k = from + (int)lround((double)(target - from) * t / fade_ms);
					timeout = FADE_STEP_MS;
				}
			}
			if (k != cur || reapply) {
				apply(k);
				cur = k;
				reapply = false;
				if (verbose)
					msg("applied %d K", k);
			}
		}
		if (eof)
			release_and_exit();

		while (wl_display_prepare_read(display) != 0)
			if (wl_display_dispatch_pending(display) < 0)
				die("compositor connection lost");
		if (wl_display_flush(display) < 0 && errno != EAGAIN) {
			wl_display_cancel_read(display);
			die("compositor connection lost: %s", strerror(errno));
		}
		struct pollfd p[2] = {
			{ .fd = wl_display_get_fd(display), .events = POLLIN },
			{ .fd = STDIN_FILENO, .events = POLLIN },
		};
		int r = poll(p, 2, timeout);
		if (r < 0 && errno != EINTR) {
			wl_display_cancel_read(display);
			die("poll: %s", strerror(errno));
		}
		if (r > 0 && (p[0].revents & (POLLIN | POLLHUP | POLLERR))) {
			if (wl_display_read_events(display) < 0)
				die("compositor connection lost");
			if (wl_display_dispatch_pending(display) < 0)
				die("compositor connection lost");
		} else {
			wl_display_cancel_read(display);
		}
		check_failed();

		if (r <= 0 || !(p[1].revents & (POLLIN | POLLHUP | POLLERR)))
			continue;
		ssize_t got = read(STDIN_FILENO, buf + used, sizeof(buf) - 1 - used);
		if (got < 0) {
			if (errno == EINTR || errno == EAGAIN)
				continue;
			die("reading stdin: %s", strerror(errno));
		}
		if (got == 0) {  /* EOF: a last line without newline still counts */
			eof = true;
			if (used && !discard)
				buf[used++] = '\n';
		}
		used += (size_t)got;
		/* take every complete line; only the last valid one matters */
		int newest = -1;
		char *line = buf, *nl;
		while ((nl = memchr(line, '\n', used - (size_t)(line - buf)))) {
			*nl = 0;
			if (discard) {
				discard = false;
			} else {
				int r = gain_r, b = gain_b;
				int k = parse_line(line, &r, &b);
				if (k < 0) {
					msg("ignoring invalid line \"%s\" (want Kelvin %d-%d, optionally red/blue %d-100)", line,
					    KELVIN_MIN, KELVIN_MAX, GAIN_MIN);
				} else {
					if (r != gain_r || b != gain_b) {  /* balance changed: re-apply what is on screen */
						gain_r = r;
						gain_b = b;
						reapply = true;
					}
					if (k > 0)
						newest = k;
				}
			}
			line = nl + 1;
		}
		used -= (size_t)(line - buf);
		memmove(buf, line, used);
		if (used == sizeof(buf) - 1) {  /* overlong line: drop it up to its newline */
			msg("ignoring overlong line");
			used = 0;
			discard = true;
		}
		if (newest > 0) {
			if (fade_ms > 0 && newest != cur)
				msg("%d K (fading from %d K over %ld ms)", newest, cur, fade_ms);
			else
				msg("%d K", newest);
			from = cur;
			target = newest;
			fade_start = now_ms();
		}
	}
}
