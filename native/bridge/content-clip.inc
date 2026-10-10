/* xdg window geometry and input regions do not clip subsurface pixels.
 * Crop HWC's existing viewports to the desktop content rectangle, including
 * while a resize preview waits for Android to relayout. Keep HWC coordinates
 * unchanged in our records so subsequent frames, input and growth can recover. */
static int content_clip_enabled(struct object *s) {
  return s && s->app && !s->full_ui && !game_fit_enabled(s);
}

static int content_clip_layer(struct object *sub, int x, int y, int w, int h) {
  struct object *layer = sub->surface;
  struct object *vp = layer ? layer->viewport : 0;
  if (!content_clip_enabled(sub->parent) || !vp || vp->width < 1 || vp->height < 1 ||
      vp->source_width < 1 || vp->source_height < 1 || w < 1 || h < 1)
    return 0;
  int left = x > sub->x ? x - sub->x : 0;
  int top = y > sub->y ? y - sub->y : 0;
  int right = x + w - sub->x, bottom = y + h - sub->y;
  if (right > vp->width) right = vp->width;
  if (bottom > vp->height) bottom = vp->height;
  if (left >= right || top >= bottom) {
    if (!layer->clip_hidden) {
      union argument empty[3] = {{.o=0},{.i=0},{.i=0}};
      wayland_send(layer->p, 1, empty);
      layer->clip_hidden = 1;
    }
    csd_input(layer->p, 0, 0, 0, 0);
    return 1;
  }
  if (layer->clip_hidden) {
    /* A destroyed wl_buffer cannot be reattached. The next HWC attach will
     * restore that layer; never retain a dangling buffer proxy. */
    if (!layer->attached_buffer) return 1;
    union argument attach[3] = {{.o=layer->attached_buffer},
      {.i=layer->attach_x},{.i=layer->attach_y}};
    wayland_send(layer->p, 1, attach);
    layer->clip_hidden = 0;
  }
  int sx = (int)((int64_t)left * vp->source_width / vp->width);
  int sy = (int)((int64_t)top * vp->source_height / vp->height);
  /* Round the source outwards: a stretched 1x1 color layer can have a
   * visible strip narrower than one unit of Wayland's 24.8 fixed point. */
  int ex = (int)(((int64_t)right * vp->source_width + vp->width-1) / vp->width);
  int ey = (int)(((int64_t)bottom * vp->source_height + vp->height-1) / vp->height);
  if (ex <= sx || ey <= sy) return 0;
  union argument source[4] = {{.i=vp->source_x+sx},{.i=vp->source_y+sy},
    {.i=ex-sx},{.i=ey-sy}};
  union argument destination[2] = {{.i=right-left},{.i=bottom-top}};
  union argument position[2] = {{.i=sub->x+left},{.i=sub->y+top}};
  wayland_send(vp->p, 1, source);
  wayland_send(vp->p, 2, destination);
  wayland_send(sub->p, 1, position);
  layer->clip_x = left; layer->clip_y = top;
  csd_input(layer->p, 0, 0, right-left, bottom-top);
  /* Source/destination changes can reveal pixels without new Android damage. */
  union argument damage[4] = {{.i=0},{.i=0},{.i=right-left},{.i=bottom-top}};
  wayland_send(layer->p, 2, damage);
  return 1;
}

static void content_clip_sync(struct object *s, int x, int y, int w, int h) {
  if (!content_clip_enabled(s)) return;
  for (struct object *sub=objects; sub; sub=sub->next) {
    if (sub->parent == s && sub->surface &&
        !strcmp(sub->p->interface->name, "wl_subsurface") &&
        content_clip_layer(sub, x, y, w, h))
      wayland_send(sub->surface->p, 6, 0);
  }
}

static void content_clip_commit(struct object *layer) {
  for (struct object *sub=objects; sub; sub=sub->next) {
    struct object *s = sub->parent;
    if (sub->surface == layer && content_clip_enabled(s) && s->has_bounds &&
        !strcmp(sub->p->interface->name, "wl_subsurface")) {
      content_clip_layer(sub, s->bx, s->by, s->bw, s->bh);
      return;
    }
  }
}

static int32_t content_clip_input(struct proxy *p, int32_t value, int axis) {
  struct object *layer = find(p);
  if (!layer) return value;
  int64_t n = (int64_t)value + 256LL * (axis ? layer->clip_y : layer->clip_x);
  return n > INT32_MAX ? INT32_MAX : n < INT32_MIN ? INT32_MIN : (int32_t)n;
}
