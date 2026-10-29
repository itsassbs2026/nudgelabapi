<?php

/**
 * @file
 * Settings: admin/config/services/nudgelab-training.
 */

/**
 * Form builder: where the API is, the signing key, and the on/off switch.
 */
function nudgelab_training_settings_form($form, &$form_state) {
  $form['nudgelab_training_enabled'] = array(
    '#type' => 'checkbox',
    '#title' => t('Show "My training" to employees'),
    '#default_value' => variable_get('nudgelab_training_enabled', 1),
    '#description' => t('Off hides the page and the block without uninstalling anything.'),
  );
  $form['nudgelab_training_api_url'] = array(
    '#type' => 'textfield',
    '#title' => t('NudgeLab API'),
    '#default_value' => variable_get('nudgelab_training_api_url', 'https://nudgelabapi.myprimeportal.com/app/v1'),
    '#description' => t('The app endpoints, ending in /app/v1.'),
    '#required' => TRUE,
  );
  $form['nudgelab_training_key_path'] = array(
    '#type' => 'textfield',
    '#title' => t('Signing key file'),
    '#default_value' => variable_get('nudgelab_training_key_path', ''),
    '#description' => t('Full path to the private key (PEM) on this server, outside the web root, readable by the web server only. Its public key goes in the API\'s APP_PASS_PUBLIC_KEYS.'),
    '#required' => TRUE,
  );
  $form['nudgelab_training_kid'] = array(
    '#type' => 'textfield',
    '#title' => t('Key id'),
    '#default_value' => variable_get('nudgelab_training_kid', 'portal-1'),
    '#description' => t('The name the API lists the public key under, e.g. portal-1.'),
    '#required' => TRUE,
  );
  $form['test'] = array(
    '#type' => 'submit',
    '#value' => t('Save and test with my account'),
    '#submit' => array('system_settings_form_submit', 'nudgelab_training_settings_test'),
    '#weight' => 100,
  );
  return system_settings_form($form);
}

/**
 * Form validation: the key must be readable and be an EC P-256 private key.
 */
function nudgelab_training_settings_form_validate($form, &$form_state) {
  $values = $form_state['values'];
  if (strpos($values['nudgelab_training_api_url'], 'https://') !== 0 && strpos($values['nudgelab_training_api_url'], 'http://127.0.0.1') !== 0) {
    form_set_error('nudgelab_training_api_url', t('Use the https:// address of the API.'));
  }
  $pem = @file_get_contents($values['nudgelab_training_key_path']);
  $key = $pem === FALSE ? FALSE : openssl_pkey_get_private($pem);
  $details = $key ? openssl_pkey_get_details($key) : FALSE;
  if (!$details || $details['type'] !== OPENSSL_KEYTYPE_EC || $details['ec']['curve_name'] !== 'prime256v1') {
    form_set_error('nudgelab_training_key_path', t('That file can\'t be read, or isn\'t an EC P-256 private key.'));
  }
  if (!preg_match('/^[A-Za-z0-9._-]{1,64}$/', $values['nudgelab_training_kid'])) {
    form_set_error('nudgelab_training_kid', t('Letters, digits, dots, dashes and underscores only.'));
  }
}

/**
 * Submit: ask the API for the admin's own list, and say what happened.
 */
function nudgelab_training_settings_test($form, &$form_state) {
  global $user;
  try {
    $list = nudgelab_training_client()->trainings($user->uid);
    $count = isset($list['assigned_trainers']) ? count($list['assigned_trainers']) : 0;
    drupal_set_message(t('Connected. The API lists @count training(s) for your account (uid @uid).', array('@count' => $count, '@uid' => $user->uid)));
  }
  catch (NudgelabClientException $e) {
    drupal_set_message(t('The API answered @status @code: @message', array(
      '@status' => $e->status ?: t('nothing'),
      '@code' => $e->apiCode,
      '@message' => $e->getMessage(),
    )), 'error');
  }
}
