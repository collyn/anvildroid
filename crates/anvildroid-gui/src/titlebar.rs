use gtk::{gdk, prelude::*};
use std::{cell::Cell, rc::Rc};
use tauri::Manager;

/// App-owned chrome: independent of desktop titlebutton icons and drag settings.
pub fn install(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let Some(window) = app.get_webview_window("main") else {
        return Ok(());
    };
    let native = window.gtk_window()?;
    native.connect_map_event(|_, _| {
        crate::startup_mark("window-mapped");
        gtk::glib::Propagation::Proceed
    });
    native.style_context().add_class("anvildroid-main");
    let webview = window.clone();
    native.connect_window_state_event(move |_, event| {
        if event.new_window_state().contains(gdk::WindowState::ICONIFIED)
            && crate::preferences::minimize_to_panel(webview.app_handle())
        {
            let _ = webview.hide();
        }
        let square = event
            .new_window_state()
            .intersects(gdk::WindowState::MAXIMIZED | gdk::WindowState::FULLSCREEN);
        let _ = webview.eval(&format!(
            "document.documentElement.classList.toggle('window-square', {square})"
        ));
        gtk::glib::Propagation::Proceed
    });
    install_native(&native, app.handle().clone());
    Ok(())
}

fn install_native(native: &gtk::ApplicationWindow, app: tauri::AppHandle) {
    let bar = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    bar.style_context().add_class("anvildroid-titlebar");
    let drag = gtk::EventBox::new();
    drag.set_hexpand(true);
    drag.set_above_child(true);
    drag.add_events(
        gdk::EventMask::BUTTON_PRESS_MASK
            | gdk::EventMask::BUTTON_RELEASE_MASK
            | gdk::EventMask::POINTER_MOTION_MASK,
    );
    let title = gtk::Label::new(native.title().as_deref());
    title.set_ellipsize(gtk::pango::EllipsizeMode::End);
    title.set_margin_start(12);
    title.set_xalign(0.0);
    drag.add(&title);
    let pressed = Rc::new(Cell::new(None::<(f64, f64, u32)>));
    let window = native.downgrade();
    let press_state = pressed.clone();
    drag.connect_button_press_event(move |_, event| {
        if event.button() != 1 {
            return gtk::glib::Propagation::Proceed;
        }
        if event.event_type() == gdk::EventType::DoubleButtonPress {
            press_state.set(None);
            if let Some(window) = window.upgrade() {
                if window.is_maximized() {
                    window.unmaximize();
                } else {
                    window.maximize();
                }
            }
        } else if event.event_type() == gdk::EventType::ButtonPress {
            let (x, y) = event.root();
            press_state.set(Some((x, y, event.time())));
        }
        gtk::glib::Propagation::Stop
    });
    let window = native.downgrade();
    let motion_state = pressed.clone();
    drag.connect_motion_notify_event(move |_, event| {
        if !event.state().contains(gdk::ModifierType::BUTTON1_MASK) {
            motion_state.set(None);
            return gtk::glib::Propagation::Proceed;
        }
        if let Some((x, y, time)) = motion_state.get() {
            let (now_x, now_y) = event.root();
            // A click must not enter KWin's move operation before double-click maximize.
            if (now_x - x).abs().max((now_y - y).abs()) >= 6.0 {
                motion_state.set(None);
                if let Some(window) = window.upgrade() {
                    window.begin_move_drag(1, x as i32, y as i32, time);
                }
            }
        }
        gtk::glib::Propagation::Stop
    });
    drag.connect_button_release_event(move |_, _| {
        pressed.set(None);
        gtk::glib::Propagation::Stop
    });
    bar.pack_start(&drag, true, true, 0);
    for (action, tooltip) in [(0, "Minimize"), (1, "Maximize"), (2, "Close")] {
        let button = gtk::Button::new();
        button.set_tooltip_text(Some(tooltip));
        button.style_context().add_class("anvildroid-window-button");
        if action == 2 {
            button.style_context().add_class("close");
        }
        let icon = gtk::DrawingArea::new();
        icon.set_size_request(36, 32);
        let window = native.downgrade();
        icon.connect_draw(move |area, cr| {
            let x = (area.allocated_width() as f64 - 10.0) / 2.0;
            let y = (area.allocated_height() as f64 - 10.0) / 2.0;
            cr.set_source_rgb(0.84, 0.89, 0.95);
            cr.set_line_width(1.25);
            match action {
                0 => {
                    cr.move_to(x, y + 8.0);
                    cr.line_to(x + 10.0, y + 8.0);
                }
                1 if window.upgrade().is_some_and(|window| window.is_maximized()) => {
                    cr.rectangle(x, y + 3.0, 7.0, 7.0);
                    cr.move_to(x + 3.0, y + 3.0);
                    cr.line_to(x + 3.0, y);
                    cr.line_to(x + 10.0, y);
                    cr.line_to(x + 10.0, y + 7.0);
                    cr.line_to(x + 7.0, y + 7.0);
                }
                1 => cr.rectangle(x, y, 10.0, 10.0),
                _ => {
                    cr.move_to(x, y);
                    cr.line_to(x + 10.0, y + 10.0);
                    cr.move_to(x + 10.0, y);
                    cr.line_to(x, y + 10.0);
                }
            }
            let _ = cr.stroke();
            gtk::glib::Propagation::Stop
        });
        if action == 1 {
            let icon = icon.clone();
            let button = button.clone();
            native.connect_window_state_event(move |window, _| {
                button.set_tooltip_text(Some(if window.is_maximized() {
                    "Restore"
                } else {
                    "Maximize"
                }));
                icon.queue_draw();
                gtk::glib::Propagation::Proceed
            });
        }
        button.add(&icon);
        let window = native.downgrade();
        let app = app.clone();
        button.connect_clicked(move |_| {
            let Some(window) = window.upgrade() else {
                return;
            };
            match action {
                0 if crate::preferences::minimize_to_panel(&app) => window.hide(),
                0 => window.iconify(),
                1 => {
                    if window.is_maximized() {
                        window.unmaximize();
                    } else {
                        window.maximize();
                    }
                }
                _ => window.close(),
            }
        });
        bar.pack_start(&button, false, false, 0);
    }
    let css = gtk::CssProvider::new();
    css.load_from_data(include_bytes!("titlebar.css"))
        .expect("valid bundled titlebar CSS");
    if let Some(screen) = gtk::prelude::GtkWindowExt::screen(native) {
        gtk::StyleContext::add_provider_for_screen(
            &screen,
            &css,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    }
    native.set_titlebar(Some(&bar));
    bar.show_all();
}
