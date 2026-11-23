import SwiftUI
import WebKit
import AuthenticationServices

// Jungle Coach for iPhone: shows the mobile web app (stats, past games, friends, coach summary)
// and handles the Google / Discord login natively with ASWebAuthenticationSession.
let SERVER = "https://junglecoach.nelkejannis.workers.dev"
let BG = UIColor(red: 0.027, green: 0.039, blue: 0.071, alpha: 1)

@main
struct JungleCoachApp: App {
    var body: some Scene {
        WindowGroup {
            WebContainer()
                .ignoresSafeArea()
                .background(Color(BG))
                .preferredColorScheme(.dark)
        }
    }
}

struct WebContainer: UIViewRepresentable {
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()          // keeps the login token between app starts
        cfg.userContentController.add(context.coordinator, name: "jcLogin")
        let web = WKWebView(frame: .zero, configuration: cfg)
        web.isOpaque = false
        web.backgroundColor = BG
        web.scrollView.backgroundColor = BG
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.allowsBackForwardNavigationGestures = true
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator        // shows JavaScript alert()/confirm() as native dialogs
        let refresh = UIRefreshControl()
        refresh.addTarget(context.coordinator, action: #selector(Coordinator.reload(_:)), for: .valueChanged)
        web.scrollView.refreshControl = refresh
        context.coordinator.web = web
        web.load(URLRequest(url: URL(string: SERVER + "/m/")!))
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate, ASWebAuthenticationPresentationContextProviding {
    weak var web: WKWebView?
    private var session: ASWebAuthenticationSession?

    @objc func reload(_ sender: UIRefreshControl) {
        web?.reload()
        sender.endRefreshing()
    }

    // The page asks for a login: open Google / Discord in the system login sheet, get the token back.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let provider = message.body as? String, ["google", "discord"].contains(provider) else { return }
        var comps = URLComponents(string: SERVER + "/auth/" + provider)!
        comps.queryItems = [URLQueryItem(name: "redirect", value: "junglecoach://auth")]
        let s = ASWebAuthenticationSession(url: comps.url!, callbackURLScheme: "junglecoach") { [weak self] url, _ in
            guard let url = url,
                  let token = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                    .queryItems?.first(where: { $0.name == "token" })?.value,
                  token.range(of: "^[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+$", options: .regularExpression) != nil
            else { return }
            DispatchQueue.main.async { self?.web?.evaluateJavaScript("window.jcSetToken('\(token)')") }
        }
        s.presentationContextProvider = self
        s.prefersEphemeralWebBrowserSession = false
        session = s
        s.start()
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.keyWindow }.first ?? ASPresentationAnchor()
    }

    private func top() -> UIViewController? {
        var vc = UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.keyWindow }.first?.rootViewController
        while let p = vc?.presentedViewController { vc = p }
        return vc
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
        if let t = top() { t.present(a, animated: true) } else { completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "Abbrechen", style: .cancel) { _ in completionHandler(false) })
        a.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
        if let t = top() { t.present(a, animated: true) } else { completionHandler(false) }
    }

    // Links to other sites (e.g. Riot, Discord) open in Safari, the app stays on Jungle Coach.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.navigationType == .linkActivated, let url = action.request.url,
           url.host != URL(string: SERVER)?.host {
            UIApplication.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
}
