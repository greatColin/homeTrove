# Requirements Document

## Introduction

Shared albums allow the HomeTrove owner to generate public links for curated albums so that visitors can browse them without logging in. The owner controls whether the visitor may view originals and download files, and may set an optional expiration date. One album can have multiple independent share links so that links can be revoked individually.

## Glossary

- **Album**: A manually curated collection of assets stored in the `albums` table.
- **Share link**: A public, opaque token that grants read-only access to one album.
- **Visitor**: An unauthenticated caller who accesses a share link.
- **Owner**: The HomeTrove administrator who creates and manages albums and their share links.

## Requirements

### Requirement 1: Create a share link

**User Story:** AS an owner, I want to create a public link for an album, so that visitors can view the album without an account.

#### Acceptance Criteria

1. WHEN the owner invokes the create-share API for an existing album, the system SHALL generate a unique opaque token and persist it with the album reference.
2. WHEN the owner provides an expiration timestamp, the system SHALL store it with the share link and reject access after the timestamp.
3. WHEN the owner enables `allow_original`, the system SHALL allow visitors to request the original media file for assets in the shared album.
4. WHEN the owner enables `allow_download`, the system SHALL set response headers that let browsers download the original media file.
5. IF the album does not exist, the system SHALL return a 404 error.

### Requirement 2: List and revoke share links

**User Story:** AS an owner, I want to list and delete share links for an album, so that I can manage access independently.

#### Acceptance Criteria

1. WHEN the owner requests the share-link list for an album, the system SHALL return all non-expired share links for that album, including token, expiration, and permission flags.
2. WHEN the owner deletes a share link, the system SHALL remove the token and reject any later request that uses the removed token.
3. IF the album does not exist, the system SHALL return a 404 error for both list and delete operations.
4. IF the share link does not belong to the requested album, the system SHALL return a 404 error on delete.

### Requirement 3: Visitor views a shared album

**User Story:** AS a visitor, I want to open a share link and see the album contents, so that I can browse the shared photos.

#### Acceptance Criteria

1. WHEN a visitor requests the public album endpoint with a valid token, the system SHALL return the album metadata and the ordered list of live assets in the album.
2. WHEN a visitor requests the public album endpoint with an expired or revoked token, the system SHALL return a 404 error.
3. WHEN the shared album contains trashed assets, the system SHALL exclude those assets from the public view.
4. WHEN the visitor requests a public thumbnail with a valid token, the system SHALL return the requested thumbnail size for an asset that belongs to the shared album.
5. IF the requested asset is not in the shared album or is trashed, the system SHALL return a 404 error for the thumbnail request.

### Requirement 4: Visitor accesses original media

**User Story:** AS a visitor, I want to view or download the original photo or video when the owner allows it.

#### Acceptance Criteria

1. WHEN the share link has `allow_original=true` and the visitor requests the original file for an album asset, the system SHALL return the file with the correct content type.
2. WHEN the share link has `allow_download=true`, the system SHALL include a `Content-Disposition: attachment` header for the original file response.
3. WHEN the share link has `allow_original=false`, the system SHALL return a 403 error for any original-file request.
4. IF the requested asset is not in the shared album or is trashed, the system SHALL return a 404 error.

### Requirement 5: URL and token design

**User Story:** AS an owner, I want short, stable share URLs that remain valid even if I later change permissions or expiration.

#### Acceptance Criteria

1. THE system SHALL store share metadata in a database table keyed by an opaque random token.
2. THE share URL SHALL contain only the opaque token, not the permission or expiration payload.
3. THE system SHALL look up the token on every access and apply the stored permissions and expiration.

## Excluded for This Iteration

- Password-protected share links.
- Per-visitor analytics or access logs.
- Editing album membership through a share link.
- Watermarked originals or size-limited previews.
