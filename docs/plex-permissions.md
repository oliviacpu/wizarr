# Plex sharing permissions

Plex settings and invitation forms show **Allow Downloads** with a short
explanation of offline copies. Permission to download does not waive Plex's
subscription requirements. The invitation form also explains **Invite to Plex
Home**: it adds a household member with profile switching, and is unnecessary
for ordinary library sharing. Public access-request approvals always use
ordinary, account-bound library sharing.

The fork no longer offers Plex **Mobile Uploads / Camera Upload** or the former
**Allow Live TV** switch in server, invitation or user forms. Camera Upload was
retired by Plex. The inherited Live TV control sent `allowChannels`, documented
by PlexAPI as access to installed channels; it did not set Plex's modern tuner
permission. Renaming that switch would not make it control Live TV correctly.
Manage native tuner access in Plex's Home settings instead. Plex restricts that
access to Home members.

An external Live TV proxy can apply separate access rules. These Wizarr controls
do not configure such a proxy or revoke access it grants. Configure proxy access
in that system; no proxy integration is implied by enabling library sharing.

Jellyfin and Emby keep their supported Live TV controls, and Emby keeps mobile
uploads. Unsupported server-form controls are disabled as well as hidden, so a
checked option from another server type is not submitted for Plex. Stored legacy
fields and API compatibility are retained; this UI cleanup needs no migration
and does not rewrite existing shares or user permissions.

References:

- [Plex library restrictions](https://support.plex.tv/articles/204232573-restricting-the-shares/)
- [Plex Downloads FAQ](https://support.plex.tv/articles/downloads-sync-faq/)
- [Plex Camera Upload retirement](https://forums.plex.tv/t/camera-upload-add-flag-to-keep-original-file-name/183813)
- [PlexAPI sharing parameters](https://python-plexapi.readthedocs.io/en/latest/modules/myplex.html#plexapi.myplex.MyPlexAccount.inviteFriend)
