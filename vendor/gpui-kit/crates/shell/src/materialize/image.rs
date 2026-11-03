//! HERDR: share images within a window and retire unused sources after a short grace.

use std::{collections::HashMap, rc::Rc, sync::Arc, time::Duration};

use gpui::{
    App, AppContext as _, BorrowAppContext as _, Entity, Global, ImageCacheItem, ImageSource,
    Resource, Task, WindowId,
};

const GRACE: Duration = Duration::from_secs(5);

#[derive(Default)]
struct Caches(HashMap<WindowId, Entity<Cache>>);
impl Global for Caches {}

struct Cache {
    images: HashMap<(Resource, u64), (Rc<ImageCacheItem>, bool)>,
    current: HashMap<Resource, u64>,
    next: u64,
    _clock: Task<()>,
}

impl Cache {
    fn new(cx: &mut App) -> Entity<Self> {
        cx.new(|cx| {
            cx.on_release(|this: &mut Self, cx| {
                for (_, (image, _)) in this.images.drain() {
                    if let Some(Ok(image)) = image.get() {
                        cx.drop_image(image, None);
                    }
                }
            })
            .detach();
            let clock = cx.spawn(async move |this, cx| {
                loop {
                    cx.background_executor().timer(GRACE).await;
                    if this
                        .update(cx, |this, cx| {
                            this.images.retain(|_, (image, unused)| {
                                if Rc::strong_count(image) > 1 {
                                    *unused = false;
                                    return true;
                                }
                                if !*unused {
                                    *unused = true;
                                    return true;
                                }
                                if let Some(Ok(image)) = image.get() {
                                    cx.drop_image(image, None);
                                }
                                false
                            });
                            this.current.retain(|resource, generation| this.images.contains_key(&(resource.clone(), *generation)));
                        })
                        .is_err()
                    {
                        break;
                    }
                }
            });
            Self {
                images: HashMap::new(),
                current: HashMap::new(),
                next: 0,
                _clock: clock,
            }
        })
    }
}

pub fn invalidate(resource: &Resource, cx: &mut App) {
    let Some(caches) = cx.try_global::<Caches>() else { return };
    let caches: Vec<_> = caches.0.values().cloned().collect();
    for cache in caches {
        cache.update(cx, |cache, _| { cache.current.remove(resource); });
    }
}

pub(super) fn source(path: &str) -> ImageSource {
    let ImageSource::Resource(mut resource) = ImageSource::from(path) else {
        unreachable!("a path is an image resource")
    };
    if let Resource::Embedded(path) = &mut resource {
        *path = crate::AppAssets::cache_key(path);
    }
    ImageSource::Custom(Arc::new(move |window, cx| {
        if !cx.has_global::<Caches>() {
            cx.set_global(Caches::default());
            cx.on_window_closed(|cx, id| {
                cx.global_mut::<Caches>().0.remove(&id);
            })
            .detach();
        }
        let cache = cx.update_global::<Caches, _>(|caches, cx| {
            caches
                .0
                .entry(window.window_handle().window_id())
                .or_insert_with(|| Cache::new(cx))
                .clone()
        });
        cache.update(cx, |cache, cx| {
            let generation = *cache.current.entry(resource.clone()).or_insert_with(|| {
                cache.next += 1;
                cache.next
            });
            let (image, unused) = cache
                .images
                .entry((resource.clone(), generation))
                .or_insert_with(|| (Rc::new(ImageCacheItem::new(&resource, cx)), false));
            *unused = false;
            // Cached GPUI subtrees keep these leases even when they skip layout and paint.
            window.use_keyed_state(("shell-image", gpui::hash(&(resource.clone(), generation))), cx, |_, _| {
                image.clone()
            });
            image.use_image(window)
        })
    }))
}

#[cfg(feature = "render-test-support")]
pub(crate) fn loaded(window: &gpui::Window, cx: &App) -> Vec<Arc<gpui::RenderImage>> {
    cx.try_global::<Caches>()
        .and_then(|caches| caches.0.get(&window.window_handle().window_id()))
        .map(|cache| {
            cache
                .read(cx)
                .images
                .values()
                .filter_map(|(image, _)| image.get()?.ok())
                .collect()
        })
        .unwrap_or_default()
}
