# Requirements Document

## Introduction

Replace the fixed-aspect-ratio thumbnail grid on the timeline and album detail pages with a justified layout that packs photos into rows of equal height while preserving each image's aspect ratio. Virtual scrolling is added so the browser only renders the rows that are currently in or near the viewport, keeping the UI responsive for libraries with tens of thousands of items.

## Glossary

- **Justified layout**: A grid where each row has the same target height and images are scaled so their combined width fills the available container width, with small uniform gaps.
- **Virtual scrolling**: Rendering only the visible slice of a long list plus a small buffer of off-screen rows, and translating the visible slice to simulate continuous scrolling.
- **Row**: A horizontal group of one or more asset thumbnails laid out together.
- **Asset**: A media item returned by `/api/assets` or an album's `asset_ids`.

## Requirements

### Requirement 1: Justified layout on the timeline

**User Story:** AS a user, I want the timeline to display photos in tightly packed rows without cropping them to a fixed aspect ratio, so that I can see the whole thumbnail.

#### Acceptance Criteria

1. WHEN the timeline page renders a set of assets, the system SHALL arrange them into rows where every thumbnail in a row has the same height.
2. WHEN the combined width of a row's assets is less than the container width, the system SHALL scale the row's assets so the row fills the container width.
3. WHEN an asset's aspect ratio is unknown, the system SHALL fall back to a default aspect ratio of 4:3 for layout purposes.
4. WHEN the container width changes, the system SHALL recompute the layout without reloading asset data.

### Requirement 2: Virtual scrolling on the timeline

**User Story:** AS a user with a large library, I want the timeline to scroll smoothly, so that performance stays acceptable as the number of assets grows.

#### Acceptance Criteria

1. WHEN the timeline contains more rows than fit in the viewport, the system SHALL render only the rows in the viewport plus a configurable buffer of rows above and below.
2. WHEN the user scrolls the timeline, the system SHALL update the rendered row slice and translate the content so the scroll position remains continuous.
3. WHEN the user reaches near the bottom of the currently loaded items, the system SHALL fetch the next page from `/api/assets` and append it to the layout.
4. WHEN a thumbnail enters the rendered slice, the system SHALL load its image lazily.

### Requirement 3: Reuse on the album detail and shared album pages

**User Story:** AS a user, I want the album and shared-album views to use the same layout as the timeline, so that the browsing experience is consistent.

#### Acceptance Criteria

1. WHEN the album detail page renders its assets, the system SHALL use the same justified virtual grid component as the timeline.
2. WHEN the shared album page renders its assets, the system SHALL use the same justified virtual grid component as the timeline.
3. WHEN the album or shared album contains no assets, the system SHALL display the existing empty message.

### Requirement 4: Preserve existing interactions

**User Story:** AS a user, I want the existing selection, favorite, preview, and bulk actions to keep working after the layout change.

#### Acceptance Criteria

1. WHEN the user clicks a thumbnail in the justified grid, the system SHALL open the existing lightbox preview or toggle selection mode as before.
2. WHEN the user hovers a thumbnail, the system SHALL show the existing favorite and video-duration overlays.
3. WHEN selection mode is active, the system SHALL display the selection badge and the bulk action bar as before.
4. WHEN the user toggles a favorite, the system SHALL apply the same optimistic update as before.

### Requirement 5: Keyboard and accessibility

**User Story:** AS a user, I want the timeline to remain keyboard-friendly, so that I can navigate without a mouse.

#### Acceptance Criteria

1. WHEN the timeline has focus, the user SHALL be able to press `Escape` to close the lightbox or exit selection mode.
2. WHEN a thumbnail receives focus, the system SHALL scroll it into view if it lies outside the rendered slice.

## Excluded for This Iteration

- Drag-and-drop reordering in albums.
- Animated layout transitions when resizing.
- Precomputed layout persistence on the server.
