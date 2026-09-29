# Comparateur Android : socle MVP

Scan EAN → offres classées par **prix total net** (prix − promo + livraison).

## Dépendances (app/build.gradle.kts)

```kotlin
plugins { id("org.jetbrains.kotlin.plugin.serialization") }

dependencies {
    // Compose (BOM) + lifecycle
    implementation(platform("androidx.compose:compose-bom:2024.10.00"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    // Caméra + scan hors-ligne
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
    implementation("androidx.camera:camera-view:1.3.4")
    implementation("com.google.mlkit:barcode-scanning:17.3.0")
    // Réseau
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.jakewharton.retrofit:retrofit2-kotlinx-serialization-converter:1.0.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    testImplementation(kotlin("test"))
}
```

Manifest : `<uses-permission android:name="android.permission.CAMERA"/>` et `INTERNET`.

## Contrat d'API attendu du backend

`GET /v1/products/{ean}/offers?country=FR&postal=75001` renvoie
`{ ean, title, imageUrl, offers: [{ merchant, priceCents, shippingCents, freeShippingOverCents, inStock, trackedUrl, promoCode, promoDiscountCents }] }`

`trackedUrl` pointe vers votre redirection `/out/{offre}` (tracking + lien d'affiliation).

## Lancer

1. Backend : `cd backend && python3 server.py` (données d'exemple incluses), tests : `python3 -m unittest test_server`.
2. App : ouvrir ce dossier dans Android Studio (il génère le wrapper Gradle), lancer sur émulateur. Le code-barres d'exemple est `4006381333931`.

## Ce qui reste à brancher avec vos comptes

- Flux réels : créez vos comptes d'affiliation (Awin, Effiliation…), téléchargez leurs flux CSV et appelez `ingest_csv` / `ingest_rows` ; mettez votre ID dans `AFFILIATE_PARAMS`.
- Push : Firebase (FCM) dans l'app + Admin SDK dans `fcm_notify`.
- Prod : hébergement HTTPS, `PUBLIC_URL`, et retrait de `usesCleartextTraffic`.
