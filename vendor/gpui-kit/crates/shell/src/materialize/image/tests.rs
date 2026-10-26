use super::*;
use gpui::{
    Context, IntoElement, ParentElement as _, Render, RenderImage,
    Styled as _, TestAppContext, VisualTestContext, Window, div,
    http_client::{AsyncBody, FakeHttpClient, Response},
    px,
};
use std::{cell::RefCell, rc::Rc, sync::Mutex};

const PNG: &[u8] = &[
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0xf4, 0x22, 0x7f,
    0x8a, 0x00, 0x00, 0x00, 0x11, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0x14, 0x32, 0x09, 0xfb,
    0xcf, 0xc0, 0xc0, 0xc0, 0x00, 0x00, 0x09, 0x0d, 0x01, 0x9d, 0xf7, 0x66, 0xcd, 0x15, 0x00, 0x00,
    0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
];

struct Picture {
    path: Option<&'static str>,
    observed: Rc<RefCell<Option<Arc<RenderImage>>>>,
}

impl Render for Picture {
    fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
        let Some(path) = self.path else { return div() };
        let ImageSource::Custom(load) = source(path) else {
            unreachable!()
        };
        let observed = self.observed.clone();
        let image = ImageSource::Custom(Arc::new(move |window, cx| {
            let result = load(window, cx);
            if let Some(Ok(image)) = &result {
                *observed.borrow_mut() = Some(image.clone());
            }
            result
        }));
        // Two uses of one source must share their load and survive repeated layout/paint.
        div()
            .flex()
            .children((0..2).map(|_| gpui::img(image.clone()).size(px(16.))))
    }
}

fn draw(cx: &mut VisualTestContext) {
    for _ in 0..4 {
        cx.update(|window, cx| window.draw(cx).clear(cx));
        cx.run_until_parked();
    }
}

#[gpui::test]
fn changing_images_release_decoded_data_and_atlas_entries(cx: &mut TestAppContext) {
    let requests = Arc::new(Mutex::new(Vec::new()));
    let recorded = requests.clone();
    let client = FakeHttpClient::create(move |request| {
        recorded.lock().unwrap().push(request.uri().to_string());
        async {
            Ok(Response::builder()
                .status(200)
                .body(AsyncBody::from(PNG.to_vec()))
                .unwrap())
        }
    });
    cx.update(|cx| cx.set_http_client(client));
    let observed = Rc::new(RefCell::new(None));
    let saved = observed.clone();
    let (view, context) = cx.add_window_view(move |_, _| Picture {
        path: Some("https://images.example/first.png"),
        observed: saved,
    });
    draw(context);
    let first = observed.borrow().clone().expect("first image loaded");
    context.update(|window, _| assert!(window.has_image_atlas_entry(&first)));
    view.update(context, |_, cx| cx.notify());
    draw(context);
    assert_eq!(
        requests.lock().unwrap().len(),
        1,
        "unchanged source is reused"
    );

    view.update(context, |view, cx| {
        view.path = Some("https://images.example/second.png");
        cx.notify();
    });
    draw(context);
    let second = observed.borrow().clone().expect("second image loaded");
    assert_ne!(first.id, second.id);
    context.update(|window, _| {
        assert!(
            !window.has_image_atlas_entry(&first),
            "replaced texture released"
        );
        assert!(window.has_image_atlas_entry(&second));
    });
    let retired = Arc::downgrade(&first);
    drop(first);
    assert!(
        retired.upgrade().is_none(),
        "replaced decoded data released"
    );

    view.update(context, |view, cx| {
        view.path = None;
        cx.notify();
    });
    draw(context);
    context.update(|window, _| assert!(!window.has_image_atlas_entry(&second)));
    observed.borrow_mut().take();
    let retired = Arc::downgrade(&second);
    drop(second);
    assert!(retired.upgrade().is_none(), "removed decoded data released");
}
