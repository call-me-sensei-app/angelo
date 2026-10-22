/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * macOS notifications for Angelo's background jobs (see `bpy_angelo_desktop.hh`), through
 * the UserNotifications framework, so they carry the application's name and icon and follow
 * the person's Notification settings for it.
 *
 * The framework needs a bundled, signed application: `currentNotificationCenter` raises for a
 * process without a bundle identifier, and an ad-hoc signed build may be refused. Both report
 * "unavailable" and nothing is posted. Without a delegate the framework drops a notification
 * while the application is in front; the delegate here shows it anyway, because a long job
 * usually ends while the person is elsewhere even when Angelo is the frontmost application
 * (another display, a locked screen).
 */

#include "bpy_angelo_desktop.hh"

#import <AppKit/AppKit.h>
#import <UserNotifications/UserNotifications.h>

#include <atomic>

namespace blender {

enum class Permission : int { Unknown = 0, NotDetermined, Authorized, Denied, Unavailable };

/* Written by the framework's completion handlers on their own threads, read on the main thread. */
static std::atomic<int> permission_state{int(Permission::Unknown)};
static std::atomic<bool> permission_requested{false};

static const char *permission_name(const Permission value)
{
  switch (value) {
    case Permission::NotDetermined:
      return "not_determined";
    case Permission::Authorized:
      return "authorized";
    case Permission::Denied:
      return "denied";
    case Permission::Unavailable:
      return "unavailable";
    case Permission::Unknown:
      break;
  }
  return "unknown";
}

}  // namespace blender

@interface AngeloNotificationDelegate : NSObject <UNUserNotificationCenterDelegate>
@end

@implementation AngeloNotificationDelegate

- (void)userNotificationCenter:(UNUserNotificationCenter *)center
       willPresentNotification:(UNNotification *)notification
         withCompletionHandler:(void (^)(UNNotificationPresentationOptions options))completionHandler
{
  completionHandler(UNNotificationPresentationOptionBanner | UNNotificationPresentationOptionList |
                    UNNotificationPresentationOptionSound);
}

- (void)userNotificationCenter:(UNUserNotificationCenter *)center
    didReceiveNotificationResponse:(UNNotificationResponse *)response
             withCompletionHandler:(void (^)(void))completionHandler
{
  /* macOS brings the application to the front itself when the notification is clicked. */
  completionHandler();
}

@end

namespace blender {

/** The shared center with Angelo's delegate, or nil where the framework cannot serve this process. */
static UNUserNotificationCenter *notification_center()
{
  static UNUserNotificationCenter *center = nil;
  static bool tried = false;
  if (tried) {
    return center;
  }
  tried = true;
  NSBundle *bundle = [NSBundle mainBundle];
  if (NSApp == nil || bundle.bundleIdentifier == nil ||
      ![bundle.bundlePath.pathExtension isEqualToString:@"app"])
  {
    permission_state = int(Permission::Unavailable);
    return nil;
  }
  @try {
    center = [[UNUserNotificationCenter currentNotificationCenter] retain];
  }
  @catch (NSException *) {
    center = nil;
  }
  if (center == nil) {
    permission_state = int(Permission::Unavailable);
    return nil;
  }
  /* The center holds its delegate weakly: this one lives as long as the process. */
  center.delegate = [[AngeloNotificationDelegate alloc] init];
  return center;
}

static void refresh_permission(UNUserNotificationCenter *center)
{
  [center getNotificationSettingsWithCompletionHandler:^(UNNotificationSettings *settings) {
    switch (settings.authorizationStatus) {
      case UNAuthorizationStatusNotDetermined:
        permission_state = int(Permission::NotDetermined);
        break;
      case UNAuthorizationStatusDenied:
        permission_state = int(Permission::Denied);
        break;
      default: /* Authorized, provisional or ephemeral: a notification is delivered. */
        permission_state = int(Permission::Authorized);
        break;
    }
  }];
}

const char *angelo_desktop_permission()
{
  @autoreleasepool {
    UNUserNotificationCenter *center = notification_center();
    if (center != nil) {
      refresh_permission(center);
    }
  }
  return permission_name(Permission(permission_state.load()));
}

void angelo_desktop_request_permission()
{
  @autoreleasepool {
    UNUserNotificationCenter *center = notification_center();
    if (center == nil || permission_requested.exchange(true)) {
      return;
    }
    [center requestAuthorizationWithOptions:(UNAuthorizationOptionAlert | UNAuthorizationOptionSound)
                          completionHandler:^(BOOL granted, NSError *error) {
                            /* An error is a build the system will not let post (ad-hoc signed). */
                            permission_state = int(granted ? Permission::Authorized :
                                                   error ? Permission::Unavailable :
                                                           Permission::Denied);
                          }];
  }
}

static NSString *string_or_empty(const char *text)
{
  NSString *value = text ? [NSString stringWithUTF8String:text] : nil;
  return value ? value : @"";
}

const char *angelo_desktop_notify(const char *identifier, const char *title, const char *body)
{
  @autoreleasepool {
    UNUserNotificationCenter *center = notification_center();
    const Permission state = Permission(permission_state.load());
    /* Unknown: the first settings answer has not arrived yet; posting is harmless either way. */
    if (center == nil || !(state == Permission::Authorized || state == Permission::Unknown)) {
      if (center != nil) {
        refresh_permission(center);
      }
      return permission_name(state);
    }
    UNMutableNotificationContent *content = [[[UNMutableNotificationContent alloc] init]
        autorelease];
    content.title = string_or_empty(title);
    content.body = string_or_empty(body);
    content.sound = [UNNotificationSound defaultSound];
    UNNotificationRequest *request = [UNNotificationRequest
        requestWithIdentifier:string_or_empty(identifier)
                      content:content
                      trigger:nil];
    [center addNotificationRequest:request
             withCompletionHandler:^(NSError *error) {
               if (error != nil) {
                 permission_state = int(Permission::Unavailable);
               }
             }];
  }
  return "posted";
}

const char *angelo_desktop_attention()
{
  if (NSApp == nil) {
    return "unavailable";
  }
  if (NSApp.isActive) {
    return "foreground";
  }
  [NSApp requestUserAttention:NSInformationalRequest];
  return "bounced";
}

}  // namespace blender
