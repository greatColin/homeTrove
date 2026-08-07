# Requirements Document

## Introduction

Smart albums are rule-based, automatically populated collections of assets. The owner defines a set of filter conditions (person, place, tag, time range, media type, favorite). The system evaluates the rules on every read and returns the matching live assets. v1 provides a simple JSON rule DSL without a visual drag-and-drop editor.

## Glossary

- **Smart album**: An album whose membership is determined by a stored rule expression rather than explicit `album_assets` rows.
- **Rule**: A JSON object with an `op` field (`and`, `or`, `person`, `place`, `tag`, `category`, `time`, `media_type`, `favorite`) and child fields.
- **Manual album**: The existing curated album backed by `album_assets`.
- **Live asset**: An asset whose `deleted_at` is `NULL`.

## Requirements

### Requirement 1: Create a smart album

**User Story:** AS an owner, I want to create an album from a rule, so that the collection stays up to date as the library grows.

#### Acceptance Criteria

1. WHEN the owner invokes the create-smart-album API with a name and a valid rule, the system SHALL persist the album and its rule.
2. WHEN the rule contains an unsupported operator, the system SHALL return a 422 error.
3. WHEN the rule references a person, place, tag, or category that does not exist, the system SHALL still accept the rule and return an empty result set for that branch.
4. IF the album name is blank, the system SHALL return a 422 error.

### Requirement 2: Evaluate and list smart album assets

**User Story:** AS an owner, I want to open a smart album and see all matching assets, so that I can browse the dynamic collection.

#### Acceptance Criteria

1. WHEN the owner requests a smart album, the system SHALL evaluate the stored rule against live assets and return the matching asset identifiers.
2. WHEN the rule matches many assets, the system SHALL return the results ordered by `taken_at` descending, then `id` descending.
3. WHEN a smart album is listed alongside manual albums, the system SHALL include its computed asset count.
4. WHEN the owner requests assets from a smart album, the system SHALL exclude trashed assets even if the rule matches them.

### Requirement 3: Supported rule operators

**User Story:** AS an owner, I want to filter by people, places, tags, categories, time, media type, and favorite status, so that the smart album matches my intent.

#### Acceptance Criteria

1. THE system SHALL support a `person` operator that matches assets containing a face linked to the given `person_id`.
2. THE system SHALL support a `place` operator that matches assets whose GPS coordinates fall inside the given place grid cell.
3. THE system SHALL support `tag` and `category` operators that match assets whose `mock.tags` or `mock.category` plugin result contains the given value.
4. THE system SHALL support a `time` operator with optional `after` and `before` epoch seconds that matches assets whose `taken_at` falls inside the range.
5. THE system SHALL support a `media_type` operator that matches assets whose `media_type` equals `image`, `video`, or `other`.
6. THE system SHALL support a `favorite` operator that matches assets whose `favorite` flag equals the given boolean.
7. THE system SHALL support `and` and `or` operators that combine a list of child rules.

### Requirement 4: Edit and delete smart albums

**User Story:** AS an owner, I want to rename a smart album or change its rule, so that the collection evolves with my needs.

#### Acceptance Criteria

1. WHEN the owner updates a smart album name, the system SHALL persist the new name.
2. WHEN the owner updates a smart album rule, the system SHALL persist the new rule and immediately return updated results.
3. WHEN the owner deletes a smart album, the system SHALL remove the rule and the album entry.

### Requirement 5: Distinguish smart and manual albums

**User Story:** AS an owner, I want to tell at a glance which albums are manual and which are rule-based, so that I know which collections update automatically.

#### Acceptance Criteria

1. THE system SHALL expose an `is_smart` boolean on every album list item and album detail response.
2. THE system SHALL not allow adding or removing individual assets from a smart album via the manual album membership endpoints.
3. THE system SHALL not allow reordering assets inside a smart album.

## Excluded for This Iteration

- Visual rule builder with drag-and-drop nodes.
- Negation (`not`) operators.
- Full-text or semantic query rules.
- Smart album sharing (reuse the existing album share feature only after a later iteration validates combined behavior).
- Nesting rules deeper than three levels.
