/* Focused check for the hotkey-poll-block patch. Builds RetroArch's real
 * input/input_driver.c (included below) against a stub joypad whose state
 * changes only when input_driver_poll() polls it, as SDL drains its event
 * queue, and replays the order of one RetroArch frame: runloop_check_state()
 * clears the block flags and decides them again from the previous poll, then
 * the core polls (late polling: on its first input_state call) and reads
 * input.
 *
 * Without the patch, a Menu + R2 chord that lands in a single poll (how Leaf's
 * input proxy sends every chord) hands R2 to the core for one frame. gambatte
 * and gpSP turn that frame into a hold-to-fast-forward override that fights
 * the fast-forward toggle.
 *
 * Run through scripts/check-hotkey-poll-block-patch.sh. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include INPUT_DRIVER_SOURCE

/* --- stub joypad -------------------------------------------------------- */

/* Leaf's MLP1 binds: input_enable_hotkey_btn = "5" (Menu) and R2 on the
 * trigger axis, input_r2_axis = "+5", as the pad's autoconfig maps it. */
#define PAD_MENU 5
#define PAD_A    0
#define PAD_R2_AXIS 5

enum { HELD_MENU = 1 << 0, HELD_A = 1 << 1, HELD_R2 = 1 << 2 };

static unsigned queued; /* written to the device, not yet polled */
static unsigned polled; /* what the last poll read */

static void pad_poll(void) { polled = queued; }

static int32_t pad_button(unsigned port, uint16_t joykey)
{
   if (port != 0)
      return 0;
   if (joykey == PAD_MENU)
      return (polled & HELD_MENU) ? 1 : 0;
   if (joykey == PAD_A)
      return (polled & HELD_A) ? 1 : 0;
   return 0;
}

static int16_t pad_axis(unsigned port, uint32_t joyaxis)
{
   if (port != 0 || joyaxis != AXIS_POS(PAD_R2_AXIS))
      return 0;
   return (polled & HELD_R2) ? 0x7fff : 0;
}

/* The shape of sdl_joypad_state(): a bitmask over the RetroPad binds. */
static int16_t pad_state(rarch_joypad_info_t *joypad_info,
      const struct retro_keybind *binds, unsigned port)
{
   unsigned i;
   int16_t ret = 0;

   for (i = 0; i < RARCH_FIRST_CUSTOM_BIND; i++)
   {
      const uint64_t joykey  = (binds[i].joykey != NO_BTN)
         ? binds[i].joykey  : joypad_info->auto_binds[i].joykey;
      const uint32_t joyaxis = (binds[i].joyaxis != AXIS_NONE)
         ? binds[i].joyaxis : joypad_info->auto_binds[i].joyaxis;

      if (     ((uint16_t)joykey != NO_BTN
               && pad_button(joypad_info->joy_idx, (uint16_t)joykey))
            || (joyaxis != AXIS_NONE
               && ((float)abs(pad_axis(joypad_info->joy_idx, joyaxis))
                  / 0x8000) > joypad_info->axis_threshold))
         ret |= (1 << i);
   }
   return ret;
}

static const input_device_driver_t pad = {
   NULL,       /* init */
   NULL,       /* query_pad */
   NULL,       /* destroy */
   pad_button,
   pad_state,
   NULL,       /* get_buttons */
   pad_axis,
   pad_poll,
   NULL,       /* set_rumble */
   NULL,       /* set_rumble_gain */
   NULL,       /* set_sensor_state */
   NULL,       /* get_sensor_input */
   NULL,       /* name */
   "stub",
};

/* --- the rest of RetroArch, as far as input_driver.c reaches ------------- */

static settings_t settings;
static runloop_state_t runloop;

settings_t *config_get_ptr(void) { return &settings; }
runloop_state_t *runloop_state_get_ptr(void) { return &runloop; }

/* Referenced by input_driver.c functions this check never calls. */
const unsigned char lr_char_props[256];
const struct input_bind_map input_config_bind_map[RARCH_BIND_LIST_END_NULL];
const struct input_key_map input_config_key_map[1];

void RARCH_LOG(const char *fmt, ...) { }
void RARCH_WARN(const char *fmt, ...) { }
void RARCH_ERR(const char *fmt, ...) { }
void RARCH_LOG_OUTPUT(const char *msg, ...) { }
const char *char_list_new_special(enum string_list_type type,
      void *data) { return NULL; }
bool command_event(enum event_command cmd, void *data) { return false; }
struct config_entry_list *config_get_entry(
      const config_file_t *conf, const char *key) { return NULL; }
int driver_find_index(const char *label, const char *drv) { return -1; }
size_t fill_pathname_join_delim(char *s, const char *dir,
      const char *path, const char delim, size_t len) { return 0; }
bool input_autoconfigure_connect(const char *name, const char *display_name,
      const char *phys, const char *driver, unsigned port, unsigned vid,
      unsigned pid) { return false; }
bool input_config_bind_map_get_valid(unsigned bind_index) { return false; }
const char *input_config_get_prefix(unsigned user, bool meta) { return ""; }
void input_config_parse_joy_axis(char *s, void *conf_data, const char *prefix,
      const char *axis, void *bind_data) { }
void input_config_parse_joy_button(char *s, void *data, const char *prefix,
      const char *btn, void *bind_data) { }
void input_config_parse_mouse_button(char *s, void *conf_data,
      const char *prefix, const char *btn, void *bind_data) { }
void input_config_reset_autoconfig_binds(unsigned port) { }
void input_keymaps_translate_rk_to_str(enum retro_key key,
      char *s, size_t len) { }
bool input_remapping_save_file(const char *path) { return false; }
const char *msg_hash_to_str(enum msg_hash_enums msg) { return ""; }
bool retroarch_override_setting_is_set(enum rarch_override_setting enum_idx,
      void *data) { return false; }
void string_copy_only_ascii(char *str_stripped, const char *str) { }
uint32_t utf8_walk(const char **string) { return 0; }

/* --- frame model -------------------------------------------------------- */

static void clear_bind(struct retro_keybind *bind)
{
   bind->key     = RETROK_UNKNOWN;
   bind->mbutton = NO_BTN;
   bind->joykey  = NO_BTN;
   bind->joyaxis = AXIS_NONE;
}

static void setup(unsigned block_delay, bool hotkey_bound)
{
   unsigned i;

   memset(&settings, 0, sizeof(settings));
   settings.uints.input_max_users          = 1;
   settings.uints.input_hotkey_block_delay = block_delay;
   settings.uints.input_libretro_device[0] = RETRO_DEVICE_JOYPAD;
   settings.uints.input_joypad_index[0]    = 0;
   settings.bools.input_remap_binds_enable = true;
   settings.floats.input_axis_threshold    = 0.5f;
   for (i = 0; i < MAX_USERS; i++)
      settings.uints.input_remap_port_map[0][i] = MAX_USERS;
   settings.uints.input_remap_port_map[0][0] = 0;
   for (i = 0; i < RARCH_FIRST_CUSTOM_BIND; i++)
      settings.uints.input_remap_ids[0][i] = i;

   for (i = 0; i < RARCH_BIND_LIST_END; i++)
   {
      clear_bind(&input_config_binds[0][i]);
      clear_bind(&input_autoconf_binds[0][i]);
      input_config_binds[0][i].valid   = true;
      input_autoconf_binds[0][i].valid = true;
   }
   input_config_binds[0][RETRO_DEVICE_ID_JOYPAD_A].joykey   = PAD_A;
   input_config_binds[0][RETRO_DEVICE_ID_JOYPAD_R2].joyaxis = AXIS_POS(PAD_R2_AXIS);
   if (hotkey_bound)
      input_config_binds[0][RARCH_ENABLE_HOTKEY].joykey     = PAD_MENU;

   memset(&input_driver_st, 0, sizeof(input_driver_st));
   input_driver_st.primary_joypad          = &pad;
   input_driver_st.libretro_input_binds[0] = &input_config_binds[0];

   queued = polled = 0;
}

struct core_view
{
   bool r2;      /* input_state(port 0, JOYPAD, R2) */
   bool r2_mask; /* R2 in input_state(port 0, JOYPAD, MASK) */
   bool a;
};

/* One frame: runloop_check_state(), then core_run() with late polling. */
static struct core_view frame(void)
{
   struct core_view view;
   input_bits_t current_bits;
   int16_t mask;

   BIT256_CLEAR_ALL(current_bits);
   input_driver_st.flags &= ~(INP_FLAG_BLOCK_LIBRETRO_INPUT
                            | INP_FLAG_BLOCK_HOTKEY);
   input_driver_collect_system_input(&input_driver_st, &settings, &current_bits);

   /* core_input_state_poll_late(): the first input_state call polls. */
   input_driver_poll();
   mask         = input_driver_state_wrapper(0, RETRO_DEVICE_JOYPAD, 0,
         RETRO_DEVICE_ID_JOYPAD_MASK);
   view.r2      = input_driver_state_wrapper(0, RETRO_DEVICE_JOYPAD, 0,
         RETRO_DEVICE_ID_JOYPAD_R2) != 0;
   view.a       = input_driver_state_wrapper(0, RETRO_DEVICE_JOYPAD, 0,
         RETRO_DEVICE_ID_JOYPAD_A) != 0;
   view.r2_mask = (mask & (1 << RETRO_DEVICE_ID_JOYPAD_R2)) != 0;
   return view;
}

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

int main(void)
{
   struct core_view v;
   int i;
   bool r2_reached;

   /* Menu and R2 in one poll, block delay 0 (what Jawaka writes for the
    * Menu modifier): the core must never see R2. */
   setup(0, true);
   frame();
   queued = HELD_MENU | HELD_R2;
   for (i = 0; i < 4; i++)
   {
      v = frame();
      CHECK(!v.r2 && !v.r2_mask,
            "chord in one poll: the core read R2 on held frame %d", i + 1);
   }
   queued = 0;
   for (i = 0; i < 2; i++)
   {
      v = frame();
      CHECK(!v.r2 && !v.r2_mask,
            "chord in one poll: the core read R2 after the release");
   }

   /* Menu first, R2 a poll later (a person on a stock pad): blocked. */
   setup(0, true);
   frame();
   queued = HELD_MENU;
   frame();
   queued = HELD_MENU | HELD_R2;
   v = frame();
   CHECK(!v.r2 && !v.r2_mask, "Menu then R2: the core read R2");

   /* Without Menu, game input is untouched, from its first frame. */
   setup(0, true);
   frame();
   queued = HELD_R2 | HELD_A;
   v = frame();
   CHECK(v.r2 && v.r2_mask && v.a,
         "R2 and A without Menu: the core read R2 %d (mask %d), A %d",
         v.r2, v.r2_mask, v.a);

   /* Letting go of Menu with R2 still held hands R2 back to the core: the
    * poll-time block must not outlive the hold. RetroArch already keeps one
    * blocked frame after the release, because runloop_check_state() still
    * sees Menu from the previous poll. */
   setup(0, true);
   frame();
   queued = HELD_MENU | HELD_R2;
   frame();
   frame();
   queued = HELD_R2;
   frame();
   v = frame();
   CHECK(v.r2 && v.r2_mask,
         "R2 held after Menu let go: the core still does not see it");

   /* No 'enable_hotkey' bind: nothing to block on. */
   setup(0, false);
   frame();
   queued = HELD_MENU | HELD_R2;
   v = frame();
   CHECK(v.r2 && v.r2_mask, "no hotkey bind: the core lost R2");

   /* A nonzero block delay lets input through on purpose (for a modifier
    * that is also a game button), and keeps doing so: R2 reaches the core
    * on the chord's first frame and is blocked once the delay runs out. */
   setup(5, true);
   frame();
   queued = HELD_MENU | HELD_R2;
   v = frame();
   CHECK(v.r2 && v.r2_mask,
         "block delay 5: the chord's first frame no longer reaches the core");
   r2_reached = true;
   for (i = 0; i < 8; i++)
      r2_reached = frame().r2;
   CHECK(!r2_reached, "block delay 5: R2 is never blocked");

   if (failures)
   {
      printf("%d check(s) failed\n", failures);
      return 1;
   }
   printf("PASS hotkey-poll-block-patch-test\n");
   return 0;
}
