/* The EGL 1.4 surface calls sdl_gl_ctx.c's swap interval emulation makes,
 * values as in Khronos egl.h. check.c defines the functions. */
#ifndef WAYLAND_SWAP_INTERVAL_STUB_EGL_H
#define WAYLAND_SWAP_INTERVAL_STUB_EGL_H

#include <stdint.h>

typedef unsigned int EGLBoolean;
typedef int32_t EGLint;
typedef void *EGLDisplay;
typedef void *EGLSurface;

#define EGL_NO_DISPLAY        ((EGLDisplay)0)
#define EGL_NO_SURFACE        ((EGLSurface)0)
#define EGL_SWAP_BEHAVIOR     0x3093
#define EGL_BUFFER_PRESERVED  0x3094
#define EGL_BUFFER_DESTROYED  0x3095
#define EGL_DRAW              0x3059

EGLDisplay eglGetCurrentDisplay(void);
EGLSurface eglGetCurrentSurface(EGLint readdraw);
EGLBoolean eglSurfaceAttrib(EGLDisplay dpy, EGLSurface surface,
      EGLint attribute, EGLint value);
EGLBoolean eglQuerySurface(EGLDisplay dpy, EGLSurface surface,
      EGLint attribute, EGLint *value);

#endif
