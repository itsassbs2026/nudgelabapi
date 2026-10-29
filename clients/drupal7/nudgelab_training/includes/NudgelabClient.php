<?php

/**
 * @file
 * Talks to the NudgeLab API for the signed-in employee (no Drupal calls here, so it can be tested on its own).
 *
 * Every request carries a NudgeLab pass: a short-lived ES256 JWT this server signs for the employee's uid with the
 * portal's private key. The API holds only the public key (docs/PORTAL_TRAINING.md in the nudgelabapi repo). The
 * pass never leaves this server: the browser only ever gets the one-time Start link.
 *
 * Written for PHP 7.2 (the portal) and up: no typed properties, arrow functions or match.
 */

class NudgelabClientException extends Exception {

  /** @var int HTTP status (0: no answer). */
  public $status;

  /** @var string The API's error code, such as "trainers_busy". */
  public $apiCode;

  public function __construct($message, $status = 0, $apiCode = '') {
    parent::__construct($message);
    $this->status = $status;
    $this->apiCode = $apiCode;
  }

}

class NudgelabClient {

  const PASS_SECONDS = 300;
  const TIMEOUT_SECONDS = 10;

  private $apiUrl;
  private $keyPath;
  private $kid;
  private $http;

  /**
   * @param string $api_url
   *   The API's app endpoints, e.g. https://nudgelabapi.myprimeportal.com/app/v1
   * @param string $key_path
   *   The portal's EC P-256 private key (PEM), outside the web root.
   * @param string $kid
   *   The key's id, as listed in the API's APP_PASS_PUBLIC_KEYS (e.g. "portal-2026").
   * @param callable|null $http
   *   function ($method, $url, $headers, $body, $timeout) returning [status, body]; the module passes
   *   drupal_http_request. Defaults to curl.
   */
  public function __construct($api_url, $key_path, $kid, $http = NULL) {
    $this->apiUrl = rtrim($api_url, '/');
    $this->keyPath = $key_path;
    $this->kid = $kid;
    $this->http = $http ?: array($this, 'curlRequest');
  }

  /** The employee's trainings (the same list the app shows). */
  public function trainings($uid) {
    return $this->call('GET', '/trainings', $uid);
  }

  /** How many are still to do, and how many are overdue: ['incompleted_count' => n, 'overdue_count' => n]. */
  public function pendingCount($uid) {
    return $this->call('GET', '/trainings/pending-count', $uid);
  }

  /**
   * A one-time Start link (works once, for about a minute) to the training page for this training.
   *
   * @return array ['code' => ..., 'url' => ..., 'expires_in' => seconds]
   */
  public function startLink($uid, $training_id, $return_url = NULL, $start_over = FALSE) {
    $body = array('training_id' => (string) $training_id, 'start_over' => (bool) $start_over);
    if ($return_url) {
      $body['return_url'] = (string) $return_url;
    }
    return $this->call('POST', '/handoffs', $uid, $body);
  }

  /** A NudgeLab pass for this uid (ES256 JWT). */
  public function pass($uid, $now = NULL) {
    $uid = (int) $uid;
    if ($uid < 1) {
      throw new NudgelabClientException('No employee is signed in.');
    }
    $now = $now === NULL ? time() : (int) $now;
    $header = array('alg' => 'ES256', 'typ' => 'JWT', 'kid' => $this->kid);
    $claims = array(
      'iss' => 'portal',
      'aud' => 'nudgelabapi',
      'sub' => (string) $uid,
      'iat' => $now,
      'exp' => $now + self::PASS_SECONDS,
      'jti' => bin2hex(random_bytes(12)),
    );
    $input = self::b64(json_encode($header)) . '.' . self::b64(json_encode($claims));
    $pem = @file_get_contents($this->keyPath);
    $key = $pem === FALSE ? FALSE : openssl_pkey_get_private($pem);
    if ($key === FALSE) {
      throw new NudgelabClientException('The NudgeLab signing key cannot be read. Check the module settings.');
    }
    $der = '';
    if (!openssl_sign($input, $der, $key, OPENSSL_ALGO_SHA256)) {
      throw new NudgelabClientException('The NudgeLab pass could not be signed.');
    }
    return $input . '.' . self::b64(self::derToJose($der));
  }

  private function call($method, $path, $uid, $body = NULL) {
    $headers = array(
      'Authorization' => 'Bearer ' . $this->pass($uid),
      'Accept' => 'application/json',
    );
    if ($body !== NULL) {
      $headers['Content-Type'] = 'application/json';
      $body = json_encode($body);
    }
    list($status, $text) = call_user_func($this->http, $method, $this->apiUrl . $path, $headers, $body, self::TIMEOUT_SECONDS);
    $data = json_decode((string) $text, TRUE);
    if ($status >= 200 && $status < 300 && is_array($data)) {
      return $data;
    }
    $code = isset($data['error']['code']) ? (string) $data['error']['code'] : '';
    $message = isset($data['error']['message']) ? (string) $data['error']['message'] : 'Trainings aren\'t available right now. Try again in a few minutes.';
    throw new NudgelabClientException($message, (int) $status, $code);
  }

  /** The default HTTP call when the module doesn't pass one (tests, other PHP apps). */
  public function curlRequest($method, $url, $headers, $body, $timeout) {
    $ch = curl_init($url);
    $lines = array();
    foreach ($headers as $name => $value) {
      $lines[] = $name . ': ' . $value;
    }
    curl_setopt_array($ch, array(
      CURLOPT_CUSTOMREQUEST => $method,
      CURLOPT_HTTPHEADER => $lines,
      CURLOPT_RETURNTRANSFER => TRUE,
      CURLOPT_TIMEOUT => $timeout,
      CURLOPT_CONNECTTIMEOUT => 5,
    ));
    if ($body !== NULL) {
      curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
    }
    $text = curl_exec($ch);
    $status = $text === FALSE ? 0 : (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return array($status, $text === FALSE ? '' : $text);
  }

  private static function b64($data) {
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
  }

  /**
   * OpenSSL gives an ECDSA signature as DER (SEQUENCE of two INTEGERs); a JWT wants the two 32-byte numbers
   * side by side (RFC 7518 §3.4).
   */
  public static function derToJose($der) {
    $pos = 0;
    if (ord($der[$pos++]) !== 0x30) {
      throw new NudgelabClientException('Unexpected signature format.');
    }
    $len = ord($der[$pos++]);
    if ($len & 0x80) {
      $pos += $len & 0x7f;
    }
    $out = '';
    for ($i = 0; $i < 2; $i++) {
      if (ord($der[$pos++]) !== 0x02) {
        throw new NudgelabClientException('Unexpected signature format.');
      }
      $n = ord($der[$pos++]);
      $int = substr($der, $pos, $n);
      $pos += $n;
      $int = ltrim($int, "\x00");
      $out .= str_pad($int, 32, "\x00", STR_PAD_LEFT);
    }
    return $out;
  }

}
