project('wallpanel-gamma', 'c',
	version: '1.1.0',
	license: 'MIT',
	meson_version: '>=0.60.0',
	default_options: ['c_std=c11', 'warning_level=2', 'werror=true'])

wayland_client = dependency('wayland-client')
wayland_scanner = find_program(dependency('wayland-scanner', native: true).get_variable('wayland_scanner'))
wlr_protocols = dependency('wlr-protocols').get_variable('pkgdatadir')
xml = wlr_protocols / 'unstable/wlr-gamma-control-unstable-v1.xml'

protocol = [
	custom_target('gamma-control-header', input: xml,
		output: 'wlr-gamma-control-unstable-v1-client-protocol.h',
		command: [wayland_scanner, 'client-header', '@INPUT@', '@OUTPUT@']),
	custom_target('gamma-control-code', input: xml,
		output: 'wlr-gamma-control-unstable-v1-protocol.c',
		command: [wayland_scanner, 'private-code', '@INPUT@', '@OUTPUT@']),
]

executable('wallpanel-gamma', 'wallpanel-gamma.c', protocol,
	c_args: ['-DVERSION="@0@"'.format(meson.project_version())],
	dependencies: [wayland_client, meson.get_compiler('c').find_library('m')],
	install: true)
