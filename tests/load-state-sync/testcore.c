/* Minimal libretro core for the LOAD_STATE_SYNC smoke.
 *
 * Its state is a fixed-size block: a magic, a frame counter, and a fill
 * pattern derived from the counter. retro_unserialize() accepts only an exact,
 * intact block and logs what it applied, so the smoke can tell "RetroArch
 * refused the file" (no log line) from "the core rejected it" and can check
 * that an OK reply restored the counter the state was saved with.
 *
 * Built and run by scripts/smoke-mlp1-load-state-sync.sh inside the MLP1
 * toolchain image. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "libretro.h"

#define TESTCORE_STATE_SIZE 4096u
#define TESTCORE_MAGIC "LSSTEST1"

static retro_environment_t environ_cb;
static retro_video_refresh_t video_cb;
static uint64_t frame_counter;
static uint16_t framebuffer[16 * 16];

static void testcore_fill(uint8_t *out, uint64_t counter)
{
   size_t i;
   memset(out, 0, TESTCORE_STATE_SIZE);
   memcpy(out, TESTCORE_MAGIC, 8);
   memcpy(out + 8, &counter, sizeof(counter));
   for (i = 16; i < TESTCORE_STATE_SIZE; i++)
      out[i] = (uint8_t)((counter + i) & 0xff);
}

RETRO_API void retro_set_environment(retro_environment_t cb)
{
   bool no_game = false;
   environ_cb   = cb;
   cb(RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME, &no_game);
}

RETRO_API void retro_set_video_refresh(retro_video_refresh_t cb) { video_cb = cb; }
RETRO_API void retro_set_audio_sample(retro_audio_sample_t cb) { (void)cb; }
RETRO_API void retro_set_audio_sample_batch(retro_audio_sample_batch_t cb) { (void)cb; }
RETRO_API void retro_set_input_poll(retro_input_poll_t cb) { (void)cb; }
RETRO_API void retro_set_input_state(retro_input_state_t cb) { (void)cb; }
RETRO_API void retro_init(void) { frame_counter = 0; }
RETRO_API void retro_deinit(void) { }
RETRO_API unsigned retro_api_version(void) { return RETRO_API_VERSION; }

RETRO_API void retro_get_system_info(struct retro_system_info *info)
{
   memset(info, 0, sizeof(*info));
   info->library_name     = "LoadStateSyncTest";
   info->library_version  = "1";
   info->valid_extensions = "bin";
   info->need_fullpath    = true;
}

RETRO_API void retro_get_system_av_info(struct retro_system_av_info *info)
{
   memset(info, 0, sizeof(*info));
   info->geometry.base_width   = 16;
   info->geometry.base_height  = 16;
   info->geometry.max_width    = 16;
   info->geometry.max_height   = 16;
   info->geometry.aspect_ratio = 1.0f;
   info->timing.fps            = 60.0;
   info->timing.sample_rate    = 48000.0;
}

RETRO_API void retro_set_controller_port_device(unsigned port, unsigned device)
{
   (void)port;
   (void)device;
}

RETRO_API void retro_reset(void) { frame_counter = 0; }

RETRO_API void retro_run(void)
{
   frame_counter++;
   if (video_cb)
      video_cb(framebuffer, 16, 16, 16 * sizeof(uint16_t));
   /* The null video driver does not pace frames. */
   usleep(16000);
}

RETRO_API size_t retro_serialize_size(void) { return TESTCORE_STATE_SIZE; }

RETRO_API bool retro_serialize(void *data, size_t size)
{
   if (size < TESTCORE_STATE_SIZE)
      return false;
   testcore_fill((uint8_t*)data, frame_counter);
   fprintf(stderr, "testcore: serialize counter=%llu\n",
         (unsigned long long)frame_counter);
   fflush(stderr);
   return true;
}

RETRO_API bool retro_unserialize(const void *data, size_t size)
{
   uint8_t expected[TESTCORE_STATE_SIZE];
   uint64_t counter;

   if (size != TESTCORE_STATE_SIZE || memcmp(data, TESTCORE_MAGIC, 8) != 0)
   {
      fprintf(stderr, "testcore: unserialize rejected size=%zu\n", size);
      fflush(stderr);
      return false;
   }
   memcpy(&counter, (const uint8_t*)data + 8, sizeof(counter));
   testcore_fill(expected, counter);
   if (memcmp(expected, data, TESTCORE_STATE_SIZE) != 0)
   {
      fprintf(stderr, "testcore: unserialize rejected pattern\n");
      fflush(stderr);
      return false;
   }
   frame_counter = counter;
   fprintf(stderr, "testcore: unserialize ok counter=%llu\n",
         (unsigned long long)counter);
   fflush(stderr);
   return true;
}

RETRO_API void retro_cheat_reset(void) { }
RETRO_API void retro_cheat_set(unsigned index, bool enabled, const char *code)
{
   (void)index;
   (void)enabled;
   (void)code;
}

RETRO_API bool retro_load_game(const struct retro_game_info *game)
{
   enum retro_pixel_format fmt = RETRO_PIXEL_FORMAT_RGB565;
   (void)game;
   return environ_cb(RETRO_ENVIRONMENT_SET_PIXEL_FORMAT, &fmt);
}

RETRO_API bool retro_load_game_special(unsigned type,
      const struct retro_game_info *info, size_t num)
{
   (void)type;
   (void)info;
   (void)num;
   return false;
}

RETRO_API void retro_unload_game(void) { }
RETRO_API unsigned retro_get_region(void) { return RETRO_REGION_NTSC; }
RETRO_API void *retro_get_memory_data(unsigned id) { (void)id; return NULL; }
RETRO_API size_t retro_get_memory_size(unsigned id) { (void)id; return 0; }
