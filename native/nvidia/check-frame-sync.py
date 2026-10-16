#!/usr/bin/env python3
"""Exercise the actual pinned HWC cache/copy functions with delayed producers.

Pass the patched hwcomposer source directory. No Android, GPU or desktop is
opened: the harness substitutes only the transport and producer fence.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def check(source):
    hwc = (source / 'hwcomposer.cpp').read_text()
    gralloc = (source / 'gralloc_handler.cpp').read_text()
    header = (source / 'wayland-hwc.h').read_text()
    metadata = header[header.index('struct buffer_metadata {'):header.index('struct hwc_present;')]
    harness = r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
using buffer_handle_t = const void *;
struct hwc_layer_1_t {buffer_handle_t handle; int acquireFenceFd=-1;
    int blending=0, flags=0, compositionType=0;};
struct cros_gralloc_handle {int fds[1]; uint32_t height=48, width=64,
    pixel_stride=64, droid_format=1;};
struct gralloc_handle_t {int prime_fd; uint32_t height=48, width=64,
    stride=64, format=1;};
constexpr int HWC_BLENDING_NONE=0, HWC_IS_CURSOR_LAYER=1,
    HWC_FRAMEBUFFER_TARGET=2, HAL_PIXEL_FORMAT_RGBA_8888=1,
    HAL_PIXEL_FORMAT_RGBX_8888=2, HAL_PIXEL_FORMAT_BGRA_8888=3;
static bool produced=false, fail_fence=false, present_supported=true;
static int waits=0, copies=0, source_pixel=7;
static std::vector<int> order;
[[maybe_unused]] static int sync_wait(int fd, int timeout) {
    assert(fd>=0 && timeout>0); ++waits; order.push_back(1);
    if(fail_fence){errno=ETIME;return -1;}
    produced=true;source_pixel=29;return 0;
}
template<class... T> static void log(const char *, T...) {}
#define ALOGW(...) log(__VA_ARGS__)
#define ALOGI(...) log(__VA_ARGS__)
#define ALOGE(...) log(__VA_ARGS__)
'''
    harness += metadata
    harness += r'''
struct display;
'''
    harness += function(gralloc, 'buffer_metadata get_buffer_metadata_cros(')
    harness += function(gralloc, 'buffer_metadata get_buffer_metadata_gbm(')
    harness += r'''
struct buffer {buffer_metadata metadata;buffer_handle_t handle;
    bool present=true,isShm=false;int unused_frames=0,pixel=0;};
struct window {buffer *last_layer_buffer=nullptr;std::vector<buffer*> presented_buffers;};
struct display {std::map<buffer_handle_t,std::unique_ptr<buffer>> buffer_map;
    std::map<int,std::unique_ptr<window>> windows;};
struct gralloc_handler {
    buffer_metadata get_buffer_metadata(display *d,hwc_layer_1_t *l,size_t p) const
        {return get_buffer_metadata_cros(d,l,p);}
    bool displayable_buffer(display *,buffer_handle_t) const{return false;}
    std::unique_ptr<buffer> create_buffer(display *,const buffer_metadata &m,buffer_handle_t h) const;
    void update_shm_buffer(display *,buffer *b) const
        {assert(produced);order.push_back(3);b->pixel=source_pixel;++copies;}
};
static std::unique_ptr<buffer> create_present_wl_buffer(display *,const buffer_metadata &m,buffer_handle_t h) {
    assert(produced);if(!present_supported)return nullptr;
    order.push_back(2);auto b=std::make_unique<buffer>();
    b->metadata=m;b->handle=h;return b;
}
std::unique_ptr<buffer> gralloc_handler::create_buffer(display *d,const buffer_metadata &m,buffer_handle_t h) const
    {return create_present_wl_buffer(d,m,h);}
static std::unique_ptr<buffer> create_shm_wl_buffer(display *d,const buffer_metadata &m,buffer_handle_t h)
    {(void)d;assert(produced);order.push_back(2);auto b=std::make_unique<buffer>();
    b->metadata=m;b->handle=h;b->present=false;b->isShm=true;return b;}
struct cursor_handler {
    template<class T> std::unique_ptr<buffer> create_buffer(T *,const buffer_metadata &m,hwc_layer_1_t *l)
        {return create_present_wl_buffer(nullptr,m,l->handle);}
};
struct waydroid_hwc_composer_device_1 {struct display *display;
    struct gralloc_handler gralloc_handler;};
static cursor_handler cursor;
static int update_present_buffer(buffer *b)
    {assert(produced);order.push_back(3);b->pixel=source_pixel;++copies;return 0;}
static bool hwc_buffer_needs_shm(unsigned,bool,bool){return true;}
'''
    # The cursor handler normally belongs to display; keep the real expression.
    harness = harness.replace('std::map<int,std::unique_ptr<window>> windows;',
                              'std::map<int,std::unique_ptr<window>> windows; struct cursor_handler *cursor_handler;')
    harness += function(hwc, 'buffer *find_cached_buffer(')
    harness += function(hwc, 'buffer *get_wl_buffer(')
    harness += r'''
static int file() {FILE *f=tmpfile();assert(f);int fd=dup(fileno(f));fclose(f);return fd;}
int main(int argc,char **argv) {
    assert(argc==2);display d;d.cursor_handler=&cursor;
    waydroid_hwc_composer_device_1 device{&d,{}};
    cros_gralloc_handle handle{{file()}};hwc_layer_1_t layer{&handle};
    if(argv[1][0]=='f' || argv[1][0]=='s') {
        present_supported=argv[1][0]!='s';
        layer.acquireFenceFd=file();int fd=layer.acquireFenceFd;
        buffer *b=get_wl_buffer(&device,&layer,0);
        assert(b && b->pixel==29 && waits==1 && copies==1);
        assert(layer.acquireFenceFd==-1 && fcntl(fd,F_GETFD)==-1);
        assert((order==std::vector<int>{1,2,3}));
        produced=false;source_pixel=7;order.clear();
        layer.acquireFenceFd=file();b=get_wl_buffer(&device,&layer,0);
        assert(b && b->pixel==29 && waits==2 && copies==2);
        assert((order==std::vector<int>{1,3}));
        fail_fence=true;produced=false;layer.acquireFenceFd=file();
        assert(!get_wl_buffer(&device,&layer,0));
        assert(copies==2 && layer.acquireFenceFd==-1);
        fail_fence=false;produced=true;
        assert(get_wl_buffer(&device,&layer,0) && waits==3);
    } else {
        produced=true;buffer *b=get_wl_buffer(&device,&layer,0);assert(b);
        buffer unaffected{};
        for(int id=0;id<2;++id) {
            d.windows[id]=std::make_unique<window>();
            d.windows[id]->last_layer_buffer=b;
            d.windows[id]->presented_buffers={b,&unaffected};
        }
        auto old=get_buffer_metadata_cros(&d,&layer,0);
        int replacement=file();close(handle.fds[0]);handle.fds[0]=replacement;
        auto next=get_buffer_metadata_cros(&d,&layer,0);
        assert(old!=next); // Same native_handle address and dimensions, new allocation.
        assert(find_cached_buffer(&device,next,&handle)==nullptr);
        assert(d.buffer_map.empty());
        for(auto &[id,w]:d.windows) {
            assert(!w->last_layer_buffer && !w->presented_buffers[0]);
            assert(w->presented_buffers[1]==&unaffected);
        }
        layer.handle=&handle;assert(get_wl_buffer(&device,&layer,0));
        gralloc_handle_t gbm{file()};layer.handle=&gbm;
        old=get_buffer_metadata_gbm(&d,&layer,0);
        replacement=file();close(gbm.prime_fd);gbm.prime_fd=replacement;
        next=get_buffer_metadata_gbm(&d,&layer,0);assert(old!=next);
        close(gbm.prime_fd);
    }
    close(handle.fds[0]);puts("PASS producer fence/copy order and recycled allocation identity");
}
'''
    harness = '#include <fcntl.h>\n' + harness
    with tempfile.TemporaryDirectory(prefix='anvil-hwc-sync-') as directory:
        path = Path(directory)
        (path / 'check.cpp').write_text(harness)
        subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        str(path / 'check.cpp'), '-o', str(path / 'check')], check=True)
        results = [subprocess.run([str(path / 'check'), case], check=False).returncode
                   for case in ('fence', 'shm', 'identity')]
        if any(results):
            raise SystemExit('HWC regression failed: ' + str(results))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    check(parser.parse_args().source)
