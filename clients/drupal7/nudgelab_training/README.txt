NudgeLab training for the employee portal (Drupal 7)
=====================================================

Employees see their assigned NudgeLab voice trainings on "My training" (/my-training) and tap Start to talk to
the trainer in the browser, on a phone or a computer. Progress and Portal completions are recorded exactly as for
the Flutter app. How it works and the API side: docs/PORTAL_TRAINING.md in the nudgelabapi repo.

Needs: Drupal 7, PHP 7.2 or later with the OpenSSL and cURL extensions. The Drupal uid must be the employee's uid
(the same number NudgeLab, Wanaka and the Portal use).


1. Make the signing key (once, on the portal server)
----------------------------------------------------

  sudo mkdir -p /etc/nudgelab
  sudo openssl ecparam -name prime256v1 -genkey -noout -out /etc/nudgelab/portal-pass.pem
  sudo openssl ec -in /etc/nudgelab/portal-pass.pem -pubout -out /etc/nudgelab/portal-pass.pub.pem
  sudo chown root:www-data /etc/nudgelab/portal-pass.pem      (the group the web server runs as)
  sudo chmod 640 /etc/nudgelab/portal-pass.pem

The private key (portal-pass.pem) never leaves this server. Send the PUBLIC key (portal-pass.pub.pem) to the
NudgeLab API server; it is listed there as "portal-1" (step 3).


2. Install the module
---------------------

  Copy the nudgelab_training folder to sites/all/modules/ and enable "NudgeLab training" (Modules page, or
  drush en nudgelab_training). Enabling it lets every signed-in user see My training.


3. On the NudgeLab API server
-----------------------------

  Copy portal-pass.pub.pem to /srv/nudgelabapi/keys/portal-pass.pub.pem and add it to APP_PASS_PUBLIC_KEYS in
  the API's .env, next to Wanaka's key:

    APP_PASS_PUBLIC_KEYS=<the existing wanaka entry>,portal-1=/srv/nudgelabapi/keys/portal-pass.pub.pem

  and add the training page to CORS_ORIGINS:  ...,https://training.myprimeportal.com
  Restart the API.


4. Settings in Drupal
---------------------

  admin/config/services/nudgelab-training:
    NudgeLab API      https://nudgelabapi.myprimeportal.com/app/v1
    Signing key file  /etc/nudgelab/portal-pass.pem
    Key id            portal-1

  "Save and test with my account" calls the API with your own uid and says how many trainings it found.
  Optional: place the block "NudgeLab: trainings to do" (Structure > Blocks) to show "You have 2 trainings to do".
  The "Show My training" switch hides everything without uninstalling.


Troubleshooting
---------------

  Reports > Recent log messages, type nudgelab_training: each failed call is logged with the API's answer.
  401 invalid_pass   the key id or the public key on the API doesn't match this server's private key.
  403                that employee isn't active in NudgeLab, or the training isn't assigned to them.
  503                the API isn't configured for passes yet, or all trainers are busy (try again later).
  0 / no answer      the portal server can't reach the API (firewall, DNS).
