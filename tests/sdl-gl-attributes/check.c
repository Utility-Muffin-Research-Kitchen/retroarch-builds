/* Focused check for the sdl-gl-attributes patch. Builds the patched
 * gfx/drivers_context/sdl_gl_ctx.c against RetroArch's own headers and a stub
 * SDL whose video subsystem resets the GL context attributes when it starts,
 * as SDL_VideoInit does, then drives the gfx_ctx_sdl_gl vtable in the order
 * video_context_driver_init_first() does: bind_api first, init second.
 *
 * Without the patch, bind_api sets the attributes before SDL's video
 * subsystem exists, SDL discards them, and a build whose SDL defaults to
 * desktop GL asks for a desktop context on a GLES-only driver.
 *
 * Run through scripts/check-sdl-gl-attributes-patch.sh, which shares the stub
 * SDL and EGL headers of tests/wayland-swap-interval. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <EGL/egl.h>

#include SDL_GL_CTX_SOURCE

/* --- stub SDL ----------------------------------------------------------- */

struct gl_attributes
{
   int profile;
   int major;
   int minor;
};

/* What SDL_VideoInit leaves behind: no profile requested, GL 2.1. */
static const struct gl_attributes sdl_defaults = { 0, 2, 1 };

static struct gl_attributes attributes;
static int                  video_up;

static void video_start(void)
{
   video_up   = 1;
   attributes = sdl_defaults;
}

int SDL_GL_SetAttribute(SDL_GLattr attr, int value)
{
   /* Before SDL_VideoInit there is no driver state to store them in. */
   if (!video_up)
      return -1;
   switch (attr)
   {
      case SDL_GL_CONTEXT_PROFILE_MASK:  attributes.profile = value; break;
      case SDL_GL_CONTEXT_MAJOR_VERSION: attributes.major   = value; break;
      case SDL_GL_CONTEXT_MINOR_VERSION: attributes.minor   = value; break;
      default: return -1;
   }
   return 0;
}

int SDL_Init(Uint32 flags)
{
   if (flags & SDL_INIT_VIDEO)
      video_start();
   return 0;
}

int SDL_InitSubSystem(Uint32 flags) { return SDL_Init(flags); }
Uint32 SDL_WasInit(Uint32 flags) { return video_up ? SDL_INIT_VIDEO : 0; }

void SDL_QuitSubSystem(Uint32 flags)
{
   if (flags & SDL_INIT_VIDEO)
   {
      video_up   = 0;
      attributes = sdl_defaults;
   }
}

const char *SDL_GetError(void) { return "stub"; }

/* Reached only through vtable entries these checks never call. */
const char *SDL_GetCurrentVideoDriver(void) { return "wayland"; }
int SDL_GL_SetSwapInterval(int interval) { return 0; }
int SDL_GL_GetSwapInterval(void) { return 0; }
SDL_GLContext SDL_GL_CreateContext(SDL_Window *window) { return NULL; }
void SDL_GL_DeleteContext(SDL_GLContext context) { }
void SDL_GL_SwapWindow(SDL_Window *window) { }
SDL_Window *SDL_CreateWindow(const char *title, int x, int y, int w, int h,
      Uint32 flags) { return NULL; }
void SDL_DestroyWindow(SDL_Window *w) { }
void SDL_SetWindowSize(SDL_Window *w, int width, int height) { }
int SDL_SetWindowFullscreen(SDL_Window *w, Uint32 flags) { return 0; }
void *SDL_GL_GetProcAddress(const char *proc) { return NULL; }
int SDL_GetCurrentDisplayMode(int i, SDL_DisplayMode *mode) { return -1; }
Uint32 SDL_GetWindowFlags(SDL_Window *w) { return 0; }
void SDL_PumpEvents(void) { }
int SDL_PeepEvents(SDL_Event *events, int numevents,
      SDL_eventaction action, Uint32 min_type, Uint32 max_type) { return 0; }
void SDL_SetWindowTitle(SDL_Window *w, const char *title) { }
int SDL_ShowCursor(int toggle) { return 0; }

/* The swap interval policy is not under test here. */
EGLDisplay eglGetCurrentDisplay(void) { return EGL_NO_DISPLAY; }
EGLSurface eglGetCurrentSurface(EGLint readdraw) { return EGL_NO_SURFACE; }
EGLBoolean eglSurfaceAttrib(EGLDisplay dpy, EGLSurface surface,
      EGLint attribute, EGLint value) { return 0; }
EGLBoolean eglQuerySurface(EGLDisplay dpy, EGLSurface surface,
      EGLint attribute, EGLint *value) { return 0; }

/* --- RetroArch ---------------------------------------------------------- */

static settings_t settings;
static video_driver_state_t video_state;

settings_t *config_get_ptr(void) { return &settings; }
video_driver_state_t *video_state_get_ptr(void) { return &video_state; }
uintptr_t video_driver_display_userdata_get(void) { return 0; }
size_t video_driver_get_window_title(char *s, size_t len) { return 0; }
void RARCH_LOG(const char *fmt, ...) { }
void RARCH_WARN(const char *fmt, ...) { }

/* --- checks ------------------------------------------------------------- */

static int failures;

#define CHECK(cond, ...) do { \
   if (!(cond)) { \
      failures++; \
      printf("FAIL %s:%d: ", __FILE__, __LINE__); \
      printf(__VA_ARGS__); \
      printf("\n"); \
   } \
} while (0)

static void expect(const char *what, int profile, int major, int minor)
{
   CHECK(attributes.profile == profile && attributes.major == major
         && attributes.minor == minor,
         "%s: SDL holds profile 0x%x GL %d.%d, want profile 0x%x GL %d.%d",
         what, attributes.profile, attributes.major, attributes.minor,
         profile, major, minor);
}

/* video_context_driver_init_first(): bind_api, then init. */
static void *open_context(enum gfx_ctx_api api, unsigned major, unsigned minor)
{
   void *ctx;
   CHECK(gfx_ctx_sdl_gl.bind_api(NULL, api, major, minor), "bind_api refused");
   ctx = gfx_ctx_sdl_gl.init(NULL);
   CHECK(ctx != NULL, "init failed");
   return ctx;
}

int main(void)
{
   void *ctx;

   /* GLES 2 (the MLP1 case): SDL starts in init, after bind_api. */
   ctx = open_context(GFX_CTX_OPENGL_ES_API, 2, 0);
   expect("GLES 2.0 after init", SDL_GL_CONTEXT_PROFILE_ES, 2, 0);
   gfx_ctx_sdl_gl.destroy(ctx);

   /* Desktop GL asks for the compatibility profile. */
   ctx = open_context(GFX_CTX_OPENGL_API, 3, 1);
   expect("GL 3.1 after init", SDL_GL_CONTEXT_PROFILE_COMPATIBILITY, 3, 1);
   gfx_ctx_sdl_gl.destroy(ctx);

   /* The video subsystem going away (destroy only quits it when this driver
    * started it) and coming back must not lose the request: a context opened
    * without a new bind_api still gets it, not SDL's defaults. */
   ctx = open_context(GFX_CTX_OPENGL_ES_API, 3, 0);
   gfx_ctx_sdl_gl.destroy(ctx);
   SDL_QuitSubSystem(SDL_INIT_VIDEO);
   CHECK(attributes.profile == sdl_defaults.profile, "quit did not reset SDL");
   ctx = gfx_ctx_sdl_gl.init(NULL);
   expect("GLES 3.0 after a restart without bind_api",
         SDL_GL_CONTEXT_PROFILE_ES, 3, 0);
   gfx_ctx_sdl_gl.destroy(ctx);

   /* SDL's video subsystem already up before RetroArch asks. */
   video_start();
   ctx = open_context(GFX_CTX_OPENGL_ES_API, 2, 0);
   expect("GLES 2.0 with SDL video already up", SDL_GL_CONTEXT_PROFILE_ES, 2, 0);
   gfx_ctx_sdl_gl.destroy(ctx);

   /* A refused API leaves the last request alone. */
   ctx = open_context(GFX_CTX_OPENGL_ES_API, 2, 0);
   CHECK(!gfx_ctx_sdl_gl.bind_api(NULL, GFX_CTX_VULKAN_API, 1, 0),
         "bind_api accepted Vulkan");
   CHECK(gfx_ctx_sdl_gl.get_api(NULL) == GFX_CTX_OPENGL_ES_API,
         "a refused API changed the bound API");
   expect("after a refused API", SDL_GL_CONTEXT_PROFILE_ES, 2, 0);
   gfx_ctx_sdl_gl.destroy(ctx);

   if (failures)
   {
      printf("%d check(s) failed\n", failures);
      return 1;
   }
   printf("PASS sdl-gl-attributes-patch-test\n");
   return 0;
}
