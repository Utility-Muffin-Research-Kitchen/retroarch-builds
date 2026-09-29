/* Focused check for the wayland-swap-interval patch. Builds the patched
 * gfx/drivers_context/sdl_gl_ctx.c against RetroArch's own headers and a stub
 * SDL/EGL layer that mirrors SDL 2.28's Wayland clamp, then drives it through
 * the gfx_ctx_sdl_gl vtable in the order gl2 does.
 *
 * Run through scripts/check-wayland-swap-interval-patch.sh. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <EGL/egl.h>

#include SDL_GL_CTX_SOURCE

/* --- stub state -------------------------------------------------------- */

static const char *video_backend = "wayland";
static int         sdl_interval;
static int         have_context;
static int         preserve_supported = 1;

static int         surfaces[2];
static EGLint      behavior[2] = { EGL_BUFFER_DESTROYED, EGL_BUFFER_DESTROYED };
static int         current_surface;

static unsigned    swaps;
static unsigned    unpreserved_repeats;
static unsigned    frame_swaps;
static unsigned    logs;
static char        last_log[256];

static int         failures;

#define CHECK(cond, ...) do { \
   if (!(cond)) { \
      failures++; \
      printf("FAIL %s:%d: ", __FILE__, __LINE__); \
      printf(__VA_ARGS__); \
      printf("\n"); \
   } \
} while (0)

/* --- SDL ---------------------------------------------------------------- */

const char *SDL_GetCurrentVideoDriver(void) { return video_backend; }

int SDL_GL_SetSwapInterval(int interval)
{
   if (!have_context)
      return -1;
   /* SDL_waylandopengles.c: "if (interval > 1) interval = 1;" */
   if (!strcmp(video_backend, "wayland") && interval > 1)
      interval = 1;
   sdl_interval = interval;
   return 0;
}

int SDL_GL_GetSwapInterval(void) { return have_context ? sdl_interval : 0; }

SDL_GLContext SDL_GL_CreateContext(SDL_Window *window)
{
   have_context = 1;
   return &have_context;
}

void SDL_GL_DeleteContext(SDL_GLContext context) { have_context = 0; }

void SDL_GL_SwapWindow(SDL_Window *window)
{
   if (frame_swaps > 0 && behavior[current_surface] != EGL_BUFFER_PRESERVED)
      unpreserved_repeats++;
   frame_swaps++;
   swaps++;
}

static int window;
SDL_Window *SDL_CreateWindow(const char *title, int x, int y, int w, int h,
      Uint32 flags) { return (SDL_Window*)&window; }
void SDL_DestroyWindow(SDL_Window *w) { }
void SDL_SetWindowSize(SDL_Window *w, int width, int height) { }
int SDL_SetWindowFullscreen(SDL_Window *w, Uint32 flags) { return 0; }
Uint32 SDL_WasInit(Uint32 flags) { return SDL_INIT_VIDEO; }
int SDL_Init(Uint32 flags) { return 0; }
int SDL_InitSubSystem(Uint32 flags) { return 0; }
void SDL_QuitSubSystem(Uint32 flags) { }
const char *SDL_GetError(void) { return "stub"; }
int SDL_GL_SetAttribute(SDL_GLattr attr, int value) { return 0; }
/* Reached only through vtable entries these checks never call. */
void *SDL_GL_GetProcAddress(const char *proc) { return NULL; }
int SDL_GetCurrentDisplayMode(int i, SDL_DisplayMode *mode) { return -1; }
Uint32 SDL_GetWindowFlags(SDL_Window *w) { return 0; }
void SDL_PumpEvents(void) { }
int SDL_PeepEvents(SDL_Event *events, int numevents,
      SDL_eventaction action, Uint32 min_type, Uint32 max_type) { return 0; }
void SDL_SetWindowTitle(SDL_Window *w, const char *title) { }
int SDL_ShowCursor(int toggle) { return 0; }

/* --- EGL ---------------------------------------------------------------- */

static EGLDisplay display = (EGLDisplay)&display;

EGLDisplay eglGetCurrentDisplay(void)
{
   return have_context ? display : EGL_NO_DISPLAY;
}

EGLSurface eglGetCurrentSurface(EGLint readdraw)
{
   return have_context ? (EGLSurface)&surfaces[current_surface] : EGL_NO_SURFACE;
}

static int surface_index(EGLSurface surface)
{
   return (int*)surface - surfaces;
}

EGLBoolean eglSurfaceAttrib(EGLDisplay dpy, EGLSurface surface,
      EGLint attribute, EGLint value)
{
   if (attribute != EGL_SWAP_BEHAVIOR)
      return 0;
   /* EGL_BAD_MATCH without EGL_SWAP_BEHAVIOR_PRESERVED_BIT in the config */
   if (value == EGL_BUFFER_PRESERVED && !preserve_supported)
      return 0;
   behavior[surface_index(surface)] = value;
   return 1;
}

EGLBoolean eglQuerySurface(EGLDisplay dpy, EGLSurface surface,
      EGLint attribute, EGLint *value)
{
   *value = behavior[surface_index(surface)];
   return 1;
}

/* --- RetroArch ---------------------------------------------------------- */

static settings_t settings;
static video_driver_state_t video_state;

settings_t *config_get_ptr(void) { return &settings; }
video_driver_state_t *video_state_get_ptr(void) { return &video_state; }
uintptr_t video_driver_display_userdata_get(void) { return 0; }
size_t video_driver_get_window_title(char *s, size_t len) { return 0; }

/* Counts only the swap interval policy lines, not init banners. */
void RARCH_LOG(const char *fmt, ...)
{
   va_list ap;
   if (strncmp(fmt, "[SDL GL] Swap interval", 22))
      return;
   va_start(ap, fmt);
   vsnprintf(last_log, sizeof(last_log), fmt, ap);
   va_end(ap);
   logs++;
}

void RARCH_WARN(const char *fmt, ...) { }

/* --- helpers ------------------------------------------------------------ */

static unsigned present_frame(void *ctx)
{
   frame_swaps = 0;
   gfx_ctx_sdl_gl.swap_buffers(ctx);
   return frame_swaps;
}

/* gl2_init's order: an interval before the window exists, then the window,
 * then drivers_init's set_nonblock_state pass. */
static void *open_context(void)
{
   void *ctx = gfx_ctx_sdl_gl.init(NULL);
   gfx_ctx_sdl_gl.swap_interval(ctx, 2);
   gfx_ctx_sdl_gl.set_video_mode(ctx, 720, 960, true);
   return ctx;
}

static void reset_stubs(const char *backend)
{
   video_backend       = backend;
   sdl_interval        = 0;
   have_context        = 0;
   preserve_supported  = 1;
   behavior[0]         = EGL_BUFFER_DESTROYED;
   behavior[1]         = EGL_BUFFER_DESTROYED;
   current_surface     = 0;
   unpreserved_repeats = 0;
   logs                = 0;
   last_log[0]         = '\0';
}

/* --- checks ------------------------------------------------------------- */

static void check_interval_table(void)
{
   static const struct
   {
      const char *backend;
      int requested;
      int applied;
      unsigned swaps;
      EGLint behavior;
   } cases[] = {
      { "wayland", 0, 0, 1, EGL_BUFFER_DESTROYED },
      { "wayland", 1, 1, 1, EGL_BUFFER_DESTROYED },
      { "wayland", 2, 1, 2, EGL_BUFFER_PRESERVED },
      { "wayland", 4, 1, 4, EGL_BUFFER_PRESERVED },
      { "wayland", -1, -1, 1, EGL_BUFFER_DESTROYED }, /* adaptive vsync */
      { "x11",     0, 0, 1, EGL_BUFFER_DESTROYED },
      { "x11",     1, 1, 1, EGL_BUFFER_DESTROYED },
      { "x11",     2, 2, 1, EGL_BUFFER_DESTROYED },
      { "x11",     4, 4, 1, EGL_BUFFER_DESTROYED },
      { "kmsdrm",  2, 2, 1, EGL_BUFFER_DESTROYED },
   };
   size_t i;

   for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
   {
      void *ctx;
      reset_stubs(cases[i].backend);
      ctx = open_context();

      gfx_ctx_sdl_gl.swap_interval(ctx, cases[i].requested);
      CHECK(SDL_GL_GetSwapInterval() == cases[i].applied,
            "%s interval %d: SDL applied %d, want %d", cases[i].backend,
            cases[i].requested, SDL_GL_GetSwapInterval(), cases[i].applied);
      CHECK(present_frame(ctx) == cases[i].swaps,
            "%s interval %d: %u swaps per frame, want %u", cases[i].backend,
            cases[i].requested, frame_swaps, cases[i].swaps);
      CHECK(behavior[0] == cases[i].behavior,
            "%s interval %d: swap behavior 0x%x, want 0x%x", cases[i].backend,
            cases[i].requested, behavior[0], cases[i].behavior);
      CHECK(unpreserved_repeats == 0,
            "%s interval %d: a repeat swapped an unpreserved buffer",
            cases[i].backend, cases[i].requested);

      gfx_ctx_sdl_gl.destroy(ctx);
   }
}

/* Fast-forward drives interval 0 through set_nonblock_state and back. */
static void check_preserve_follows_duplication(void)
{
   static const struct { int interval; unsigned swaps; EGLint behavior; } steps[] = {
      { 2, 2, EGL_BUFFER_PRESERVED },
      { 0, 1, EGL_BUFFER_DESTROYED },
      { 2, 2, EGL_BUFFER_PRESERVED },
      { 1, 1, EGL_BUFFER_DESTROYED },
      { 4, 4, EGL_BUFFER_PRESERVED },
      { 2, 2, EGL_BUFFER_PRESERVED },
   };
   size_t i;
   void *ctx;

   reset_stubs("wayland");
   ctx = open_context();
   for (i = 0; i < sizeof(steps) / sizeof(steps[0]); i++)
   {
      gfx_ctx_sdl_gl.swap_interval(ctx, steps[i].interval);
      CHECK(present_frame(ctx) == steps[i].swaps,
            "step %zu interval %d: %u swaps, want %u", i, steps[i].interval,
            frame_swaps, steps[i].swaps);
      CHECK(behavior[0] == steps[i].behavior,
            "step %zu interval %d: swap behavior 0x%x, want 0x%x", i,
            steps[i].interval, behavior[0], steps[i].behavior);
   }
   CHECK(unpreserved_repeats == 0, "a repeat swapped an unpreserved buffer");
   gfx_ctx_sdl_gl.destroy(ctx);
}

static void check_reinit(void)
{
   void *ctx;

   reset_stubs("wayland");
   ctx = open_context();
   gfx_ctx_sdl_gl.swap_interval(ctx, 2);
   CHECK(present_frame(ctx) == 2, "before reinit: %u swaps, want 2", frame_swaps);
   gfx_ctx_sdl_gl.destroy(ctx);

   /* A fresh context and surface: the early gl2_init call must not engage,
    * and the first real pass must engage again. */
   behavior[0] = EGL_BUFFER_DESTROYED;
   logs        = 0;
   ctx         = gfx_ctx_sdl_gl.init(NULL);
   gfx_ctx_sdl_gl.swap_interval(ctx, 2);
   CHECK(logs == 0, "interval before the window logged: %s", last_log);
   gfx_ctx_sdl_gl.set_video_mode(ctx, 720, 960, true);
   CHECK(present_frame(ctx) == 1, "reinit before set_nonblock_state: %u swaps, want 1",
         frame_swaps);
   gfx_ctx_sdl_gl.swap_interval(ctx, 2);
   CHECK(present_frame(ctx) == 2, "after reinit: %u swaps, want 2", frame_swaps);
   CHECK(behavior[0] == EGL_BUFFER_PRESERVED, "after reinit: surface not preserved");
   gfx_ctx_sdl_gl.destroy(ctx);
}

static void check_surface_replaced(void)
{
   void *ctx;

   reset_stubs("wayland");
   ctx = open_context();
   gfx_ctx_sdl_gl.swap_interval(ctx, 2);
   present_frame(ctx);

   current_surface = 1;
   CHECK(present_frame(ctx) == 2, "replaced surface: %u swaps, want 2", frame_swaps);
   CHECK(behavior[1] == EGL_BUFFER_PRESERVED, "replaced surface not preserved");
   CHECK(unpreserved_repeats == 0, "replaced surface repeated an unpreserved buffer");

   /* Stopping must restore the surface in use, and leave the old one alone. */
   behavior[0] = EGL_BUFFER_PRESERVED;
   gfx_ctx_sdl_gl.swap_interval(ctx, 1);
   CHECK(behavior[1] == EGL_BUFFER_DESTROYED, "current surface still preserved");
   gfx_ctx_sdl_gl.destroy(ctx);
}

static void check_preserve_unsupported(void)
{
   void *ctx;

   reset_stubs("wayland");
   preserve_supported = 0;
   ctx = open_context();
   gfx_ctx_sdl_gl.swap_interval(ctx, 2);
   CHECK(present_frame(ctx) == 1, "no preserve: %u swaps, want 1", frame_swaps);
   CHECK(strstr(last_log, "could not be preserved") != NULL,
         "no preserve: log does not say so: %s", last_log);
   gfx_ctx_sdl_gl.destroy(ctx);
}

static void check_logging(void)
{
   void *ctx;
   unsigned before;

   reset_stubs("wayland");
   ctx = open_context();

   gfx_ctx_sdl_gl.swap_interval(ctx, 1);
   gfx_ctx_sdl_gl.swap_interval(ctx, 0);
   gfx_ctx_sdl_gl.swap_interval(ctx, 1);
   CHECK(logs == 0, "intervals 0 and 1 logged: %s", last_log);

   gfx_ctx_sdl_gl.swap_interval(ctx, 2);
   CHECK(logs == 1, "engaging logged %u times, want 1", logs);
   CHECK(strstr(last_log, "2 requested, 1 applied: 1 extra swap(s)") != NULL,
         "engage log: %s", last_log);

   before = logs;
   gfx_ctx_sdl_gl.swap_interval(ctx, 2);
   present_frame(ctx);
   present_frame(ctx);
   CHECK(logs == before, "unchanged policy logged again: %s", last_log);

   gfx_ctx_sdl_gl.swap_interval(ctx, 0);
   CHECK(logs == before + 1, "disengaging logged %u times, want 1", logs - before);
   gfx_ctx_sdl_gl.destroy(ctx);
}

int main(void)
{
   check_interval_table();
   check_preserve_follows_duplication();
   check_reinit();
   check_surface_replaced();
   check_preserve_unsupported();
   check_logging();

   if (failures)
   {
      printf("%d check(s) failed\n", failures);
      return 1;
   }
   printf("PASS wayland-swap-interval-patch-test (%u swaps)\n", swaps);
   return 0;
}
