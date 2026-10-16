/* SPDX-License-Identifier: MIT — XIM callbacks to text-input-v3 transactions.
 */
#pragma once
#include <cwctype>
void Server::create_ime(Top *t) {
  if (!platform.xim)
    return;
  XIMStyles *styles = nullptr;
  XGetIMValues(platform.xim, XNQueryInputStyle, &styles, nullptr);
  XIMStyle style = XIMPreeditNothing | XIMStatusNothing;
  if (styles) {
    if (getenv("ANVIL_TEST_XIM_TRACE"))
      for (unsigned i = 0; i < styles->count_styles; ++i)
        fprintf(stderr, "xim supported=%lu\n", styles->supported_styles[i]);
    for (unsigned i = 0; i < styles->count_styles; ++i)
      if (styles->supported_styles[i] ==
          (XIMPreeditCallbacks | XIMStatusNothing)) {
        style = styles->supported_styles[i];
        t->preedit_callbacks = true;
        break;
      }
    XFree(styles);
  }
  if (getenv("ANVIL_TEST_XIM_TRACE"))
    fprintf(stderr, "xim style=%lu callbacks=%d window=0x%lx\n", style,
            t->preedit_callbacks, t->window.window);
  if (t->preedit_callbacks) {
    // XIM's legacy function typedef is void(), although PreeditStart returns
    // int.
    t->ime_callbacks[0] = {
        reinterpret_cast<XPointer>(t),
        reinterpret_cast<XIMProc>(reinterpret_cast<void (*)()>(
            +[](XIC, XPointer value, XPointer) -> int {
              auto *t = reinterpret_cast<Top *>(value);
              t->preedit = {};
              return -1;
            }))};
    t->ime_callbacks[1] = {
        reinterpret_cast<XPointer>(t),
        reinterpret_cast<XIMProc>(+[](XIC, XPointer value, XPointer) {
          auto *t = reinterpret_cast<Top *>(value);
          t->preedit = {};
          t->server->ime_preedit(t);
        })};
    t->ime_callbacks[2] = {
        reinterpret_cast<XPointer>(t),
        reinterpret_cast<XIMProc>(+[](XIC, XPointer value, XPointer info) {
          auto *t = reinterpret_cast<Top *>(value);
          auto *draw = reinterpret_cast<XIMPreeditDrawCallbackStruct *>(info);
          if (!draw)
            return;
          if (getenv("ANVIL_TEST_XIM_TRACE"))
            fprintf(stderr, "xim draw active=%d count=%u first=%d length=%d\n",
                    t->server->text_active(t),
                    draw->text ? draw->text->length : 0, draw->chg_first,
                    draw->chg_length);
          try {
            std::u32string replacement;
            if (draw->text) {
              if (draw->text->encoding_is_wchar) {
                if (draw->text->string.wide_char)
                  for (unsigned i = 0; i < draw->text->length; ++i)
                    replacement += char32_t(draw->text->string.wide_char[i]);
              } else if (draw->text->string.multi_byte)
                replacement = decode_utf8(draw->text->string.multi_byte);
            }
            t->preedit.replace(draw->chg_first, draw->chg_length, replacement,
                               draw->caret);
            t->server->ime_preedit(t);
          } catch (const std::exception &) {
            t->preedit = {};
            t->server->ime_preedit(t);
          }
        })};
    t->ime_callbacks[3] = {
        reinterpret_cast<XPointer>(t),
        reinterpret_cast<XIMProc>(+[](XIC, XPointer value, XPointer info) {
          auto *t = reinterpret_cast<Top *>(value);
          auto *caret = reinterpret_cast<XIMPreeditCaretCallbackStruct *>(info);
          if (!caret)
            return;
          auto &p = t->preedit;
          switch (caret->direction) {
          case XIMAbsolutePosition:
            p.caret = caret->position;
            break;
          case XIMForwardChar:
            ++p.caret;
            break;
          case XIMBackwardChar:
            --p.caret;
            break;
          case XIMLineStart:
          case XIMPreviousLine:
            p.caret = 0;
            break;
          case XIMLineEnd:
          case XIMNextLine:
            p.caret = int(p.text.size());
            break;
          case XIMForwardWord:
            while (p.caret < int(p.text.size()) && !iswspace(p.text[p.caret]))
              ++p.caret;
            while (p.caret < int(p.text.size()) && iswspace(p.text[p.caret]))
              ++p.caret;
            break;
          case XIMBackwardWord:
            while (p.caret > 0 && iswspace(p.text[p.caret - 1]))
              --p.caret;
            while (p.caret > 0 && !iswspace(p.text[p.caret - 1]))
              --p.caret;
            break;
          default:
            break;
          }
          p.caret = std::clamp(p.caret, 0, int(p.text.size()));
          caret->position = p.caret;
          t->server->ime_preedit(t);
        })};
    auto callbacks = XVaCreateNestedList(
        0, XNPreeditStartCallback, &t->ime_callbacks[0], XNPreeditDoneCallback,
        &t->ime_callbacks[1], XNPreeditDrawCallback, &t->ime_callbacks[2],
        XNPreeditCaretCallback, &t->ime_callbacks[3], nullptr);
    t->ic = XCreateIC(platform.xim, XNInputStyle, style, XNClientWindow,
                      t->window.window, XNFocusWindow, t->window.window,
                      XNPreeditAttributes, callbacks, nullptr);
    XFree(callbacks);
  }
  if (!t->ic)
    t->ic =
        XCreateIC(platform.xim, XNInputStyle,
                  XIMPreeditNothing | XIMStatusNothing, XNClientWindow,
                  t->window.window, XNFocusWindow, t->window.window, nullptr);
  if (t->ic) {
    long mask = 0;
    XGetICValues(t->ic, XNFilterEvents, &mask, nullptr);
    XWindowAttributes attr{};
    XGetWindowAttributes(platform.display, t->window.window, &attr);
    XSelectInput(platform.display, t->window.window,
                 attr.your_event_mask | mask);
  }
}
