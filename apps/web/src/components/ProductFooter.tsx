import "@/styles/product-footer.css";

export function ProductFooter({ className }: { className: string }) {
  return <footer className={`${className} product-footer`}>
    <span className="product-footer__brand">Dralo Studio</span>
    <span className="product-footer__details">
      <span>系统开发商：广东麦佳信息技术有限责任公司</span>
      <span>软件版本：v0.1.0-preview.3</span>
    </span>
  </footer>;
}
