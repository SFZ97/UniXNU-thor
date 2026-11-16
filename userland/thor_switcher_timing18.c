#include <CoreGraphics/CGGeometry.h>
#include <dispatch/dispatch.h>
#include <mach/mach_time.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>

enum { CueLimit = 96 };
struct SwitcherCue {
    uint64_t timestamp;
    char phase;
};

static struct SwitcherCue cues[CueLimit];
static unsigned cue_count;
static uint64_t last_cue[2], spacing;
static dispatch_queue_t cue_queue;
static Class kill_scroll_view;
static void (*native_drag)(id, SEL, id);
static id (*native_kill_event)(id, SEL, long, id, double, CGPoint);

static void
emit_status(void *message)
{
    const char *line = message;
    (void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, (void *)line, strlen(line));
}

static void
write_cue(void *context)
{
    const struct SwitcherCue *cue = context;
    char text[48];
    int length = snprintf(text, sizeof(text), "Pong Switcher: %c %016llx",
        cue->phase, (unsigned long long)cue->timestamp);
    if (length > 0 && (size_t)length < sizeof(text))
        (void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, text, (size_t)length);
}

static void
queue_cue(unsigned phase)
{
    if (!pthread_main_np() || cue_count == CueLimit)
        return;
    const uint64_t timestamp = mach_absolute_time();
    if (timestamp - last_cue[phase] < spacing)
        return;
    last_cue[phase] = timestamp;
    struct SwitcherCue *cue = &cues[cue_count++];
    cue->timestamp = timestamp;
    cue->phase = phase == 0 ? 'P' : 'K';
    dispatch_async_f(cue_queue, cue, write_cue);
}

static void
trace_drag(id self, SEL cmd, id scroll_view)
{
    if (scroll_view && object_getClass(scroll_view) == kill_scroll_view)
        queue_cue(0);
    native_drag(self, cmd, scroll_view);
}

static id
trace_kill_event(id self, SEL cmd, long role, id layout, double progress, CGPoint translation)
{
    if (layout)
        queue_cue(1);
    return native_kill_event(self, cmd, role, layout, progress, translation);
}

static bool
valid_method(Class cls, SEL selector, const char *result, const char *const *arguments,
    unsigned count, Method *verified)
{
    if (!cls)
        return false;
    Method method = class_getInstanceMethod(cls, selector);
    Class superclass = class_getSuperclass(cls);
    if (!method || (superclass && method == class_getInstanceMethod(superclass, selector)) ||
        method_getNumberOfArguments(method) != count + 2)
        return false;
    char *encoding = method_copyReturnType(method);
    bool valid = encoding && !strcmp(encoding, result);
    free(encoding);
    for (unsigned i = 0; i < count && valid; ++i) {
        encoding = method_copyArgumentType(method, i + 2);
        valid = encoding && !strcmp(encoding, arguments[i]);
        free(encoding);
    }
    if (valid)
        *verified = method;
    return valid;
}

static void
install_switcher_timing(void *unused)
{
    (void)unused;
    mach_timebase_info_data_t timebase;
    if (mach_timebase_info(&timebase) != KERN_SUCCESS || !timebase.numer || !timebase.denom)
        return;
    spacing = 60000000ull * timebase.denom / timebase.numer;
    cue_queue = dispatch_get_global_queue(QOS_CLASS_UTILITY, 0);
    kill_scroll_view = objc_lookUpClass("SBFluidSwitcherTouchPassThroughScrollView");
    Class container = objc_lookUpClass("SBFluidSwitcherItemContainer");
    Class removal = objc_lookUpClass("SBSwipeToKillSwitcherModifierEvent");
    const char *const drag_args[] = { "@" };
    const char *const kill_args[] = { "q", "@", "d", "{CGPoint=dd}" };
    Method drag = NULL, kill = NULL;
    if (kill_scroll_view && valid_method(container, sel_registerName("scrollViewWillBeginDragging:"),
        "v", drag_args, 1, &drag)) {
        native_drag = (void (*)(id, SEL, id))method_getImplementation(drag);
        method_setImplementation(drag, (IMP)trace_drag);
    }
    if (valid_method(removal, sel_registerName("initWithLayoutRole:inAppLayout:progress:translation:"),
        "@", kill_args, 4, &kill)) {
        native_kill_event = (id (*)(id, SEL, long, id, double, CGPoint))method_getImplementation(kill);
        method_setImplementation(kill, (IMP)trace_kill_event);
    }
    dispatch_async_f(cue_queue, drag && kill ? "Pong Switcher: timing armed drag=1 kill=1" :
        drag ? "Pong Switcher: timing armed drag=1 kill=0" :
        kill ? "Pong Switcher: timing armed drag=0 kill=1" :
        "Pong Switcher: timing unavailable", emit_status);
}

__attribute__((visibility("hidden"))) void
thor_switcher_timing_start(void)
{
    dispatch_async_f(dispatch_get_main_queue(), NULL, install_switcher_timing);
}
