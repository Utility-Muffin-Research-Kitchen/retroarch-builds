/* Just enough of SDL2's public API for gfx/drivers_context/sdl_gl_ctx.c to
 * compile on the host. check.c defines the functions the swap interval policy
 * reaches; the rest are declared only. */
#ifndef WAYLAND_SWAP_INTERVAL_STUB_SDL_H
#define WAYLAND_SWAP_INTERVAL_STUB_SDL_H

#include <stdint.h>

#define SDL_MAJOR_VERSION 2
#define SDL_MINOR_VERSION 28
#define SDL_PATCHLEVEL    5

typedef uint32_t Uint32;
typedef struct SDL_Window SDL_Window;
typedef struct SDL_Texture SDL_Texture;
typedef struct SDL_Renderer SDL_Renderer;
typedef struct SDL_Surface SDL_Surface;
typedef void *SDL_GLContext;

#define SDL_INIT_VIDEO 0x00000020u

#define SDL_WINDOW_FULLSCREEN         0x00000001u
#define SDL_WINDOW_OPENGL             0x00000002u
#define SDL_WINDOW_INPUT_FOCUS        0x00000200u
#define SDL_WINDOW_MOUSE_FOCUS        0x00000400u
#define SDL_WINDOW_FULLSCREEN_DESKTOP (SDL_WINDOW_FULLSCREEN | 0x00001000u)
#define SDL_WINDOWPOS_UNDEFINED_DISPLAY(x) (0x1FFF0000u | (x))

typedef enum
{
   SDL_GL_CONTEXT_MAJOR_VERSION = 17,
   SDL_GL_CONTEXT_MINOR_VERSION = 18,
   SDL_GL_CONTEXT_PROFILE_MASK  = 21
} SDL_GLattr;

#define SDL_GL_CONTEXT_PROFILE_COMPATIBILITY 0x0002
#define SDL_GL_CONTEXT_PROFILE_ES            0x0004

typedef struct SDL_DisplayMode
{
   Uint32 format;
   int w;
   int h;
   int refresh_rate;
   void *driverdata;
} SDL_DisplayMode;

#define SDL_QUIT              0x100
#define SDL_APP_TERMINATING   0x101
#define SDL_WINDOWEVENT       0x200
#define SDL_WINDOWEVENT_RESIZED 5

typedef enum { SDL_ADDEVENT, SDL_PEEKEVENT, SDL_GETEVENT } SDL_eventaction;

typedef struct SDL_WindowEvent
{
   Uint32 type;
   uint8_t event;
   int data1;
   int data2;
} SDL_WindowEvent;

typedef union SDL_Event
{
   Uint32 type;
   SDL_WindowEvent window;
} SDL_Event;

int SDL_Init(Uint32 flags);
int SDL_InitSubSystem(Uint32 flags);
void SDL_QuitSubSystem(Uint32 flags);
Uint32 SDL_WasInit(Uint32 flags);
const char *SDL_GetError(void);
const char *SDL_GetCurrentVideoDriver(void);

int SDL_GL_SetAttribute(SDL_GLattr attr, int value);
int SDL_GL_SetSwapInterval(int interval);
int SDL_GL_GetSwapInterval(void);
SDL_GLContext SDL_GL_CreateContext(SDL_Window *window);
void SDL_GL_DeleteContext(SDL_GLContext context);
void SDL_GL_SwapWindow(SDL_Window *window);
void *SDL_GL_GetProcAddress(const char *proc);

SDL_Window *SDL_CreateWindow(const char *title, int x, int y, int w, int h,
      Uint32 flags);
void SDL_DestroyWindow(SDL_Window *window);
void SDL_SetWindowSize(SDL_Window *window, int w, int h);
int SDL_SetWindowFullscreen(SDL_Window *window, Uint32 flags);
void SDL_SetWindowTitle(SDL_Window *window, const char *title);
Uint32 SDL_GetWindowFlags(SDL_Window *window);
int SDL_GetCurrentDisplayMode(int display_index, SDL_DisplayMode *mode);

void SDL_PumpEvents(void);
int SDL_PeepEvents(SDL_Event *events, int numevents,
      SDL_eventaction action, Uint32 min_type, Uint32 max_type);
int SDL_ShowCursor(int toggle);

#endif
